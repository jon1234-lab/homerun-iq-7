#!/usr/bin/env python3
"""NHL player-prop daily pipeline — anytime goals, 1+ points, shots on goal.

Each run:
  1. downloads fresh MoneyPuck data (game-by-game skater logs + shot data),
  2. grades every pending pick and parlay from earlier dates using NHL boxscores,
  3. for each market, tests challenger ideas against the current champion model on games the
     models never saw; a challenger is adopted only if it wins on BOTH test windows,
  4. refits each champion on all data and projects today's slate (+ goals for the next 3 days),
  5. logs today's top picks and model parlays, and writes JSON outputs + updated state.

Usage:
  python3 daily.py --run-date YYYY-MM-DD --state STATE_DIR --out OUT_DIR --work WORK_DIR [--goalies goalies.json]
goalies.json: {"TBL": "Vasilevskiy", ...}  (team -> starting goalie last name for TODAY; optional)
"""
import argparse, csv, glob, json, os, random, subprocess, time, unicodedata, warnings
import concurrent.futures as cf
from datetime import date, timedelta
import numpy as np, pandas as pd
from scipy.stats import nbinom, poisson
from sklearn.linear_model import PoissonRegressor
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score
warnings.filterwarnings('ignore')

MP = 'https://moneypuck.com/moneypuck/playerData'
SHOTS = 'https://peter-tanner.com/moneypuck/downloads/shots_{y}.zip'
NHL = 'https://api-web.nhle.com/v1'
CUR = 2026            # MoneyPuck season label for 2026-27
SEASONS = [2023, 2024, 2025, 2026]
KEEP = ['playerId','season','name','gameId','playerTeam','opposingTeam','home_or_away','gameDate','position','situation','icetime',
        'I_F_xGoals','I_F_goals','I_F_shotsOnGoal','I_F_unblockedShotAttempts','I_F_highDangerxGoals','I_F_points',
        'OnIce_F_xGoals','OnIce_F_goals']

# market -> (target column, binary event used for scoring, display label)
MARKETS = {'goal': ('g', 1, 'Anytime goal'), 'point': ('pts', 1, '1+ point'), 'sog': ('sog', 3, '3+ shots on goal')}
# tracker / parlay leg markets -> (boxscore stat, threshold)
LEG = {'Goal': ('goals', 1), '1+ point': ('points', 1), '2+ SOG': ('sog', 2), '3+ SOG': ('sog', 3), '4+ SOG': ('sog', 4)}

def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)

def curl(url, out, timeout=60):
    r = subprocess.run(['curl', '-s', '-m', str(timeout), '-o', out, '-w', '%{http_code}', url], capture_output=True, text=True)
    return r.stdout.strip()

def get_json(url, tmp):
    for _ in range(3):
        if curl(url, tmp, 30) == '200':
            with open(tmp) as f: return json.load(f)
        time.sleep(2)
    raise RuntimeError('failed ' + url)

# ---------------------------------------------------------------- 1. data
def download(W):
    os.makedirs(f'{W}/shots', exist_ok=True); os.makedirs(f'{W}/gbg', exist_ok=True)
    for y in SEASONS:
        z = f'{W}/shots/shots_{y}.zip'; c = f'{W}/shots/shots_{y}.csv'
        if os.path.exists(c) and y != CUR: continue
        code = curl(SHOTS.format(y=y), z, 300)
        if code != '200': raise RuntimeError(f'shots {y}: {code}')
        subprocess.run(['unzip', '-o', '-q', z, '-d', f'{W}/shots'], check=True)
    ids = set()
    for y in SEASONS[1:]:
        f = f'{W}/skaters_{y}.csv'
        if y == CUR or not os.path.exists(f):
            if curl(f'{MP}/seasonSummary/{y}/regular/skaters.csv', f) != '200': raise RuntimeError(f'skaters {y}')
        for r in csv.DictReader(open(f)):
            if r['situation'] == 'all': ids.add(r['playerId'])
    log('players', len(ids))
    def one(pid):
        raw = f'{W}/gbg/{pid}.raw'; slim = f'{W}/gbg/{pid}.csv'
        code = curl(f'{MP}/careers/gameByGame/regular/skaters/{pid}.csv', raw, 60)
        if code != '200': return code
        with open(raw) as fi, open(slim, 'w', newline='') as fo:
            w = csv.writer(fo); w.writerow(KEEP)
            for x in csv.DictReader(fi):
                if int(x['season']) >= SEASONS[0] and x['situation'] in ('all', '5on4'):
                    w.writerow([x.get(k, '') for k in KEEP])
        os.remove(raw); return '200'
    todo = sorted(ids)
    for attempt in range(5):
        with cf.ThreadPoolExecutor(8) as ex: res = dict(zip(todo, ex.map(one, todo)))
        todo = [p for p, c in res.items() if c != '200']
        log('gbg pass', attempt, 'failed', len(todo))
        if not todo: break
        time.sleep(20 * (attempt + 1))
    if len(todo) > 50: raise RuntimeError(f'{len(todo)} player files failed to download')

def build_tables(W):
    fr = []
    for y in SEASONS:
        s = pd.read_csv(f'{W}/shots/shots_{y}.csv', usecols=['game_id','season','isPlayoffGame','teamCode','homeTeamCode','awayTeamCode',
                        'goalieIdForShot','goalieNameForShot','xGoal','goal','shotOnEmptyNet','time','event'], low_memory=False)
        fr.append(s[s.isPlayoffGame == 0])
    S = pd.concat(fr, ignore_index=True)
    S['defTeam'] = np.where(S.teamCode == S.homeTeamCode, S.awayTeamCode, S.homeTeamCode)
    S['gkey'] = S.season.astype(str) + '_' + S.game_id.astype(str)
    S['sog_flag'] = S.event.isin(['SHOT', 'GOAL']).astype(int)
    tg = S.groupby(['gkey', 'defTeam']).agg(xGA=('xGoal', 'sum'), GA=('goal', 'sum'), SA=('sog_flag', 'sum')).reset_index()
    G = S[(S.shotOnEmptyNet == 0) & (S.goalieIdForShot > 0)]
    gg = G.groupby(['gkey', 'defTeam', 'goalieIdForShot']).agg(xGA=('xGoal', 'sum'), GA=('goal', 'sum'), first=('time', 'min'),
                                                               name=('goalieNameForShot', 'last')).reset_index()
    st = gg.sort_values('first').groupby(['gkey', 'defTeam']).head(1)[['gkey', 'defTeam', 'goalieIdForShot', 'name']]
    tg = tg.merge(st.rename(columns={'goalieIdForShot': 'starterId', 'name': 'starterName'}), on=['gkey', 'defTeam'], how='left')
    P = pd.concat([d for d in (pd.read_csv(f) for f in glob.glob(f'{W}/gbg/*.csv')) if len(d)], ignore_index=True)
    for c in KEEP:   # empty or odd files can turn numeric columns into text; force them back
        if c not in ('name', 'playerTeam', 'opposingTeam', 'home_or_away', 'position', 'situation'): P[c] = pd.to_numeric(P[c], errors='coerce')
    P = P.dropna(subset=['playerId', 'gameId', 'gameDate'])
    P[['playerId', 'gameId', 'season', 'gameDate']] = P[['playerId', 'gameId', 'season', 'gameDate']].astype('int64')
    a = P[P.situation == 'all'].copy()
    pp = P[P.situation == '5on4'][['playerId', 'gameId', 'icetime', 'I_F_xGoals']].rename(columns={'icetime': 'pp_toi', 'I_F_xGoals': 'pp_xg'})
    a = a.merge(pp, on=['playerId', 'gameId'], how='left').fillna({'pp_toi': 0, 'pp_xg': 0})
    a['date'] = pd.to_datetime(a.gameDate.astype(str), format='%Y%m%d')
    a['gkey'] = a.season.astype(str) + '_' + a.gameId.astype(str).str[-6:].astype(int).astype(str)
    dates = a.groupby('gkey').date.min()
    tg['date'] = tg.gkey.map(dates); gg['date'] = gg.gkey.map(dates)
    return a, tg.dropna(subset=['date']), gg.dropna(subset=['date'])

# ---------------------------------------------------------------- 2. features
EW_COLS = ['toi', 'pp_toi', 'xg', 'g', 'sog', 'ush', 'hdxg', 'oixgf', 'pp_xg', 'pts', 'oigf']
def player_feats(a):
    a = a.sort_values(['playerId', 'date', 'gameId']).reset_index(drop=True)
    a['toi'] = a.icetime / 60; a['pp_toi'] = a.pp_toi / 60
    a['xg'] = a.I_F_xGoals; a['g'] = a.I_F_goals; a['sog'] = a.I_F_shotsOnGoal; a['ush'] = a.I_F_unblockedShotAttempts
    a['hdxg'] = a.I_F_highDangerxGoals; a['oixgf'] = a.OnIce_F_xGoals; a['pts'] = a.I_F_points; a['oigf'] = a.OnIce_F_goals
    sh = a.groupby('playerId', sort=False)[EW_COLS].shift(1)
    sh['playerId'] = a.playerId
    for hl in (5, 10, 20, 40):
        e = sh.groupby('playerId', sort=False)[EW_COLS].transform(lambda s: s.ewm(halflife=hl, min_periods=1).mean())
        for c in EW_COLS: a[f'{c}_e{hl}'] = e[c].values
    gp = a.groupby('playerId', sort=False)
    a['n_prior'] = gp.cumcount()
    a['_xg0'] = a.xg.fillna(0); a['_g0'] = a.g.fillna(0)
    a['cum_xg'] = a.groupby('playerId', sort=False)._xg0.cumsum() - a._xg0
    a['cum_g'] = a.groupby('playerId', sort=False)._g0.cumsum() - a._g0
    for k in (15, 25, 40): a[f'fin{k}'] = (a.cum_g + k) / (a.cum_xg + k)
    for hl in (10, 20, 40):
        a[f'xg60_e{hl}'] = a[f'xg_e{hl}'] / a[f'toi_e{hl}'] * 60
        a[f'oixgf60_e{hl}'] = a[f'oixgf_e{hl}'] / a[f'toi_e{hl}'] * 60
        a[f'sog60_e{hl}'] = a[f'sog_e{hl}'] / a[f'toi_e{hl}'] * 60
        a[f'pts60_e{hl}'] = a[f'pts_e{hl}'] / a[f'toi_e{hl}'] * 60
    a['oigf60_e20'] = a.oigf_e20 / a.toi_e20 * 60
    a['ppxg60_e40'] = np.where(a.pp_toi_e40 > 0.05, a.pp_xg_e40 / a.pp_toi_e40 * 60, 0)
    a['hd_share'] = (a.hdxg_e20 / a.xg_e20).clip(0, 1)
    gs = a.groupby(['playerId', 'season'], sort=False)
    a['s_xg'] = gs._xg0.cumsum() - a._xg0; a['s_g'] = gs._g0.cumsum() - a._g0; a['s_gp'] = gs.cumcount()
    a['due'] = a.s_xg * a.fin25 - a.s_g
    a['due_pg'] = np.where(a.s_gp > 0, a.due / a.s_gp.clip(lower=1), 0)
    a['rest'] = gp.date.diff().dt.days.clip(upper=7).fillna(7); a['b2b'] = (a.rest == 1).astype(int)
    a['home'] = (a.home_or_away == 'HOME').astype(int); a['isD'] = (a.position == 'D').astype(int)
    return a

def team_goalie_feats(tg, gg):
    tg = tg.sort_values(['defTeam', 'date']).copy(); g = tg.groupby('defTeam')
    lg = {'xGA': tg.xGA.mean(), 'SA': tg.SA.mean()}
    for hl in (10, 20):
        tg[f'opp_xga_rel{hl}'] = (g.xGA.transform(lambda s: s.shift(1).ewm(halflife=hl, min_periods=1).mean()) / lg['xGA']).fillna(1)
        tg[f'opp_sa_rel{hl}'] = (g.SA.transform(lambda s: s.shift(1).ewm(halflife=hl, min_periods=1).mean()) / lg['SA']).fillna(1)
    tg['opp_rest'] = g.date.diff().dt.days.clip(upper=7).fillna(7)
    gg = gg.sort_values(['goalieIdForShot', 'date']).copy(); g2 = gg.groupby('goalieIdForShot')
    gg['c_ga'] = (g2.GA.cumsum() - gg.GA); gg['c_xga'] = (g2.xGA.cumsum() - gg.xGA)
    gg['gk_fac'] = (gg.c_ga + 40) / (gg.c_xga + 40)
    tg = tg.merge(gg[['gkey', 'goalieIdForShot', 'gk_fac']], left_on=['gkey', 'starterId'], right_on=['gkey', 'goalieIdForShot'], how='left')
    tg['gk_fac'] = tg.gk_fac.fillna(1.0)
    return tg, gg, lg

OPP_COLS = ['opp_xga_rel10', 'opp_xga_rel20', 'opp_sa_rel10', 'opp_sa_rel20', 'gk_fac']
def attach_opp(a, tg):
    a = a.merge(tg[['gkey', 'defTeam', 'opp_rest'] + OPP_COLS], left_on=['gkey', 'opposingTeam'], right_on=['gkey', 'defTeam'], how='left')
    a['opp_b2b'] = (a.opp_rest == 1).astype(int)
    for c in OPP_COLS: a[c] = a[c].fillna(1.0)
    return a

# ---------------------------------------------------------------- 3. model terms
L = np.log
TERMS = {
    'l_toi5':  lambda d: L(d.toi_e5.clip(lower=3)),   'l_toi10': lambda d: L(d.toi_e10.clip(lower=3)),
    'l_toi20': lambda d: L(d.toi_e20.clip(lower=3)),  'l_toi40': lambda d: L(d.toi_e40.clip(lower=3)),
    'l_xg60_10': lambda d: L(d.xg60_e10.clip(lower=.02)), 'l_xg60_20': lambda d: L(d.xg60_e20.clip(lower=.02)),
    'l_xg60_40': lambda d: L(d.xg60_e40.clip(lower=.02)),
    'l_xg60_40_sq': lambda d: L(d.xg60_e40.clip(lower=.02)) ** 2,
    'l_ppx40': lambda d: L(d.ppxg60_e40.clip(lower=.05)),
    'l_pptoi5': lambda d: L(d.pp_toi_e5.clip(lower=.05)), 'l_pptoi10': lambda d: L(d.pp_toi_e10.clip(lower=.05)),
    'l_pptoi40': lambda d: L(d.pp_toi_e40.clip(lower=.05)),
    'l_fin15': lambda d: L(d.fin15), 'l_fin25': lambda d: L(d.fin25), 'l_fin40': lambda d: L(d.fin40),
    'l_opp10': lambda d: L(d.opp_xga_rel10), 'l_opp20': lambda d: L(d.opp_xga_rel20), 'l_gk': lambda d: L(d.gk_fac),
    'l_osa10': lambda d: L(d.opp_sa_rel10), 'l_osa20': lambda d: L(d.opp_sa_rel20),
    'l_oixgf10': lambda d: L(d.oixgf60_e10.clip(lower=.5)), 'l_oixgf40': lambda d: L(d.oixgf60_e40.clip(lower=.5)),
    'l_oigf20': lambda d: L(d.oigf60_e20.clip(lower=.3)),
    'l_sog10': lambda d: L(d.sog_e10.clip(lower=.1)), 'l_sog20': lambda d: L(d.sog_e20.clip(lower=.1)),
    'l_sog40': lambda d: L(d.sog_e40.clip(lower=.1)), 'l_ush10': lambda d: L(d.ush_e10.clip(lower=.1)),
    'l_sog60_10': lambda d: L(d.sog60_e10.clip(lower=.2)), 'l_sog60_40': lambda d: L(d.sog60_e40.clip(lower=.2)),
    'l_pts10': lambda d: L(d.pts_e10.fillna(0) + .05), 'l_pts40': lambda d: L(d.pts_e40.fillna(0) + .05),
    'l_pts60_40': lambda d: L(d.pts60_e40.clip(lower=.1)),
    'l_g40': lambda d: L(d.g_e40.fillna(0) + .05), 'hd_share': lambda d: d.hd_share.fillna(.3),
    'isD_x_xg60': lambda d: d.isD * L(d.xg60_e40.clip(lower=.02)),
    'home': lambda d: d.home, 'b2b': lambda d: d.b2b, 'opp_b2b': lambda d: d.opp_b2b, 'isD': lambda d: d.isD,
    'due_pg': lambda d: d.due_pg,
}
GB_COLS = ['toi_e10','toi_e40','pp_toi_e10','xg_e10','xg_e40','xg60_e10','xg60_e40','ppxg60_e40','g_e10','g_e40','sog_e10','ush_e10',
           'hdxg_e10','oixgf60_e10','fin25','cum_xg','due','due_pg','opp_xga_rel20','gk_fac','home','rest','b2b','opp_b2b','isD','n_prior',
           'pts_e10','pts_e40','oigf60_e20']
CHAMPIONS0 = {
    'goal': {'version': 'v2', 'terms': ['l_toi10','l_toi40','l_xg60_10','l_xg60_40','l_ppx40','l_pptoi10','l_fin25','l_opp20','l_gk',
             'l_oixgf10','l_sog10','home','b2b','opp_b2b','isD','due_pg'], 'alpha': 1e-4, 'gb_weight': 0.0},
    'point': {'version': 'p1', 'terms': ['l_toi10','l_toi40','l_toi5','l_pptoi10','l_pts40','l_pts10','l_oixgf10','l_oigf20','l_xg60_40',
              'l_opp20','l_gk','home','b2b','opp_b2b','isD'], 'alpha': 1e-4, 'gb_weight': 0.0},
    'sog': {'version': 's1', 'terms': ['l_toi10','l_toi40','l_toi5','l_pptoi10','l_sog10','l_sog40','l_sog60_40','l_ush10','l_osa20',
            'home','b2b','opp_b2b','isD'], 'alpha': 1e-4, 'gb_weight': 0.0},
}
IDEAS = {
    'goal': [
        ('Add very recent ice time (5-game)', 'add', ['l_toi5']), ('Add very recent power-play time', 'add', ['l_pptoi5']),
        ('Slower finishing regression (k=40)', 'swap', ['l_fin25', 'l_fin40']), ('Faster finishing regression (k=15)', 'swap', ['l_fin25', 'l_fin15']),
        ('Add high-danger shot share', 'add', ['hd_share']), ('Defenseman shot-quality interaction', 'add', ['isD_x_xg60']),
        ('Curved shot-quality effect', 'add', ['l_xg60_40_sq']), ('Add recent opponent defense (10-game)', 'add', ['l_opp10']),
        ('Add shot attempts per game', 'add', ['l_ush10']), ('Add recent raw goals (hot hand)', 'add', ['l_g40']),
        ('Drop DUE', 'drop', ['due_pg']), ('Add long-run shots on goal', 'add', ['l_sog40']),
        ('Add long-run team chances with him on ice', 'add', ['l_oixgf40']), ('Stronger regularization', 'alpha', 1e-3),
        ('Blend 30% gradient boosting', 'gb', 0.3), ('Add 20-game shot quality', 'add', ['l_xg60_20']),
        ('Add long-run power-play time', 'add', ['l_pptoi40']), ('Add opponent shots allowed', 'add', ['l_osa20'])],
    'point': [
        ('Add shot volume', 'add', ['l_sog10']), ('Add finishing skill', 'add', ['l_fin25']),
        ('Add long-run team chances with him on ice', 'add', ['l_oixgf40']), ('Drop very recent ice time', 'drop', ['l_toi5']),
        ('Add power-play shot quality', 'add', ['l_ppx40']), ('Add points per 60', 'add', ['l_pts60_40']),
        ('Add recent opponent defense (10-game)', 'add', ['l_opp10']), ('Stronger regularization', 'alpha', 1e-3),
        ('Blend 30% gradient boosting', 'gb', 0.3), ('Drop recent points form', 'drop', ['l_pts10'])],
    'sog': [
        ('Add shot quality per 60', 'add', ['l_xg60_40']), ('Add recent opponent shots allowed (10-game)', 'add', ['l_osa10']),
        ('Drop shot attempts', 'drop', ['l_ush10']), ('Add 20-game shots on goal', 'add', ['l_sog20']),
        ('Add high-danger share', 'add', ['hd_share']), ('Add very recent power-play time', 'add', ['l_pptoi5']),
        ('Add recent shots per 60', 'add', ['l_sog60_10']), ('Stronger regularization', 'alpha', 1e-3),
        ('Add opponent defense (chances allowed)', 'add', ['l_opp20'])],
}
CHALLENGERS = {'goal': 2, 'point': 1, 'sog': 1}

def X(d, terms): return pd.DataFrame({t: TERMS[t](d) for t in terms})

def fit(cfg, tr, market):
    tgt = MARKETS[market][0]
    tr = tr[tr[tgt].notna()]
    glm = make_pipeline(StandardScaler(), PoissonRegressor(alpha=cfg['alpha'], max_iter=3000)).fit(X(tr, cfg['terms']), tr[tgt])
    m = {'glm': glm, 'gb': None, 'phi': 1.0}
    if cfg.get('gb_weight', 0) > 0 and market != 'sog':
        m['gb'] = HistGradientBoostingClassifier(learning_rate=0.03, max_depth=4, min_samples_leaf=40, max_iter=400,
                                                 l2_regularization=1.0, random_state=0).fit(tr[GB_COLS], (tr[tgt] > 0).astype(int))
    if market == 'sog':
        mu = glm.predict(X(tr, cfg['terms']))
        m['phi'] = float(max(1.0, np.mean((tr[tgt].values - mu) ** 2 / np.clip(mu, .05, None))))
    return m

def lam(cfg, m, d): return m['glm'].predict(X(d, cfg['terms']))

def p_atleast(mu, k, phi):
    mu = np.clip(mu, 1e-4, None)
    if phi <= 1.02: return 1 - poisson.cdf(k - 1, mu)
    n = mu / (phi - 1); p = 1 / phi
    return 1 - nbinom.cdf(k - 1, n, p)

def prob(cfg, m, d, market, k=None):
    k = k or MARKETS[market][1]; mu = lam(cfg, m, d)
    p = p_atleast(mu, k, m['phi']) if market == 'sog' else 1 - np.exp(-mu)
    if m['gb'] is not None: p = (1 - cfg['gb_weight']) * p + cfg['gb_weight'] * m['gb'].predict_proba(d[GB_COLS])[:, 1]
    return np.clip(p, 1e-4, 1 - 1e-4)

def ybin(d, market): t, k, _ = MARKETS[market]; return (d[t] >= k).astype(int)

def apply_idea(champ, idea, market):
    lab, kind, pay = idea; c = json.loads(json.dumps(champ))
    if kind == 'add':
        if all(t in c['terms'] for t in pay): return None
        c['terms'] += [t for t in pay if t not in c['terms']]
    elif kind == 'drop':
        if not any(t in c['terms'] for t in pay) or len(c['terms']) <= 3: return None
        c['terms'] = [t for t in c['terms'] if t not in pay]
    elif kind == 'swap':
        if pay[0] not in c['terms'] or pay[1] in c['terms']: return None
        c['terms'] = [pay[1] if t == pay[0] else t for t in c['terms']]
    elif kind == 'alpha':
        if c['alpha'] == pay: return None
        c['alpha'] = pay
    elif kind == 'gb':
        if market == 'sog' or c.get('gb_weight', 0) == pay: return None
        c['gb_weight'] = pay
    return c

def windows(F):
    """A: train 2024-25, test 2025-26.  B: train everything before the last 60 game-days, test those days."""
    days = sorted(F.date.unique()); cut = days[-60] if len(days) > 60 else days[0]
    return {'A': (F[F.season == 2024], F[F.season == 2025]), 'B': (F[F.date < cut], F[F.date >= cut])}

def score(cfg, W, market):
    out = {}
    for k, (tr, te) in W.items():
        te = te[te[MARKETS[market][0]].notna()]
        out[k] = float(log_loss(ybin(te, market), prob(cfg, fit(cfg, tr, market), te, market)))
    return out

def bump(v):
    head = v.rstrip('0123456789.')
    num = v[len(head):]
    major, minor = (num.split('.') + ['0'])[:2]
    return f'{head}{major}.{int(minor) + 1}'

# ---------------------------------------------------------------- 4. grading
_RES = {}
def results_for(d, W):
    """(stats, done): stats[playerId] = {'goals','points','sog'} for every skater who played on date d."""
    if d in _RES: return _RES[d]
    sc = get_json(f'{NHL}/score/{d}', f'{W}/score_{d}.json')
    stats, done = {}, True
    for g in sc.get('games', []):
        if g.get('gameState') not in ('OFF', 'FINAL'): done = False; continue
        bx = get_json(f"{NHL}/gamecenter/{g['id']}/boxscore", f"{W}/bx_{g['id']}.json")
        for side in ('awayTeam', 'homeTeam'):
            for grp in ('forwards', 'defense'):
                for p in bx['playerByGameStats'][side].get(grp, []):
                    stats[str(p['playerId'])] = {'goals': p.get('goals', 0), 'points': p.get('points', 0), 'sog': p.get('sog', 0)}
    _RES[d] = (stats, done); return _RES[d]

def leg_hit(market, st):
    stat, k = LEG[market]; return st.get(stat, 0) >= k

def grade(tracker, run_date, W):
    for d in sorted({r['date'] for r in tracker if r['result'] == 'Pending' and r['date'] < run_date}):
        stats, done = results_for(d, W)
        for r in tracker:
            if r['date'] != d or r['result'] != 'Pending': continue
            st = stats.get(str(r['playerId']))
            if st is not None:
                r['stat'] = st[LEG[r['market']][0]]; r['result'] = 'Hit' if leg_hit(r['market'], st) else 'Miss'
            elif done: r['result'] = 'Did not play'
    return tracker

def grade_parlays(parlays, run_date, W):
    """A leg whose player did not play is voided (as books do); the parlay is graded on the remaining legs."""
    for d in sorted({r['date'] for r in parlays if r['result'] == 'Pending' and r['date'] < run_date}):
        stats, done = results_for(d, W)
        for r in parlays:
            if r['date'] != d or r['result'] != 'Pending': continue
            live, hits, unknown = [], 0, False
            for leg in r['legs']:
                st = stats.get(str(leg['playerId']))
                if st is not None:
                    h = leg_hit(leg['market'], st); live.append(leg); hits += int(h); leg['result'] = 'Hit' if h else 'Miss'
                elif done: leg['result'] = 'Did not play'
                else: unknown = True
            if unknown: continue
            if not live: r['result'] = 'Void'; continue
            r['p_graded'] = round(float(np.prod([l['p'] for l in live])) * (r.get('corr', 1.0) if len(live) == len(r['legs']) else 1.0), 4)
            r['result'] = ('Won' if hits == len(live) else 'Lost') + (' (leg voided)' if len(live) < len(r['legs']) else '')
    return parlays

def daily_summary(tracker):
    rows = {}
    for r in tracker:
        if r['result'] not in ('Hit', 'Miss'): continue
        s = rows.setdefault((r['date'], r['market']), {'date': r['date'], 'market': r['market'], 'picks': 0, 'hits': 0, 'expected': 0.0, 'brier': 0.0})
        y = int(r['result'] == 'Hit'); s['picks'] += 1; s['hits'] += y; s['expected'] += r['p']; s['brier'] += (r['p'] - y) ** 2
    return [{'key': f"{s['date']} {s['market']}", 'date': s['date'], 'market': s['market'], 'picks': s['picks'], 'hits': s['hits'],
             'expected_hits': round(s['expected'], 2), 'hit_rate': round(s['hits'] / s['picks'], 3), 'brier': round(s['brier'] / s['picks'], 4)}
            for _, s in sorted(rows.items(), reverse=True)]

def market_summary(tracker):
    out = []
    for mk in ('Goal', '1+ point', '3+ SOG'):
        g = [r for r in tracker if r['market'] == mk and r['result'] in ('Hit', 'Miss')]
        h = sum(r['result'] == 'Hit' for r in g); e = sum(r['p'] for r in g)
        out.append({'market': mk, 'graded': len(g), 'hits': h, 'expected_hits': round(e, 2),
                    'hit_rate': round(h / len(g), 3) if g else None, 'avg_p': round(e / len(g), 3) if g else None})
    return out

# ---------------------------------------------------------------- parlays
PTYPES = [  # (type label, probability key in board rows, leg market, n legs)
    ('Goals 2-leg', 'p_goal', 'Goal', 2), ('Goals 3-leg', 'p_goal', 'Goal', 3), ('Goals 4-leg', 'p_goal', 'Goal', 4),
    ('Points 2-leg', 'p_point', '1+ point', 2), ('Points 3-leg', 'p_point', '1+ point', 3),
    ('Shots 3+ 2-leg', 'p_sog3', '3+ SOG', 2), ('Shots 3+ 3-leg', 'p_sog3', '3+ SOG', 3),
    ('Shots 2+ 3-leg', 'p_sog2', '2+ SOG', 3), ('Shots 2+ 4-leg', 'p_sog2', '2+ SOG', 4)]
TEAMMATES = 'Goals teammates'
OLD_TYPES = {'2-leg, different games': 'Goals 2-leg', '3-leg, different games': 'Goals 3-leg', '4-leg, different games': 'Goals 4-leg',
             '2-leg, teammates': TEAMMATES}

def parlay_backtest(te):
    """te: player-games with p_goal, p_point, p_sog2, p_sog3 and outcomes. One parlay of each type per night, one player per game."""
    te = te.assign(h_goal=(te.g >= 1).astype(int), h_point=(te.pts >= 1).astype(int), h_sog2=(te.sog >= 2).astype(int), h_sog3=(te.sog >= 3).astype(int))
    hmap = {'Goal': 'h_goal', '1+ point': 'h_point', '2+ SOG': 'h_sog2', '3+ SOG': 'h_sog3'}
    out = []
    for lab, pcol, mk, n in PTYPES:
        pr, ht = [], []
        for _, g in te.groupby('date'):
            if g.gkey.nunique() < n: continue
            top = g.sort_values(pcol, ascending=False).drop_duplicates('gkey').head(n)
            pr.append(float(np.prod(top[pcol]))); ht.append(int(top[hmap[mk]].all()))
        out.append({'parlay': lab, 'nights': len(pr), 'predicted_pct': round(100 * float(np.mean(pr)), 1), 'actual_pct': round(100 * float(np.mean(ht)), 1)})
    pr, ht = [], []
    for _, g in te.groupby('date'):
        best = None
        for _, gt in g.groupby('playerTeam'):
            t2 = gt.sort_values('p_goal', ascending=False).head(2)
            if len(t2) == 2 and (best is None or t2.p_goal.prod() > best.p_goal.prod()): best = t2
        if best is not None: pr.append(float(best.p_goal.prod())); ht.append(int(best.h_goal.all()))
    corr = float(np.mean(ht) / np.mean(pr)) if pr else 1.0
    corr = 1.0 + 0.5 * (corr - 1.0)          # shrink halfway toward independence
    out.append({'parlay': TEAMMATES, 'nights': len(pr), 'predicted_pct': round(100 * float(np.mean(pr)), 1), 'actual_pct': round(100 * float(np.mean(ht)), 1)})
    return out, corr

def game_of(r): return '-'.join(sorted([r['team'], r['opp'][-3:]]))

def build_parlays(board_rows, run_date, versions, corr):
    out = []
    for lab, pcol, mk, n in PTYPES:
        used, legs = set(), []
        for r in sorted(board_rows, key=lambda r: -r[pcol]):
            if game_of(r) in used: continue
            used.add(game_of(r)); legs.append(r)
            if len(legs) == n: break
        if len(legs) == n:
            out.append({'date': run_date, 'type': lab, 'legs_n': n, 'corr': 1.0,
                        'legs': [{'player': l['player'], 'playerId': l['playerId'], 'team': l['team'], 'market': mk, 'p': l[pcol]} for l in legs]})
    best = None
    for t in sorted({r['team'] for r in board_rows}):
        tm = sorted([r for r in board_rows if r['team'] == t], key=lambda r: -r['p_goal'])[:2]
        if len(tm) == 2 and (best is None or tm[0]['p_goal'] * tm[1]['p_goal'] > best[0]['p_goal'] * best[1]['p_goal']): best = tm
    if best:
        out.append({'date': run_date, 'type': TEAMMATES, 'legs_n': 2, 'corr': round(corr, 3),
                    'legs': [{'player': l['player'], 'playerId': l['playerId'], 'team': l['team'], 'market': 'Goal', 'p': l['p_goal']} for l in best]})
    for pl in out:
        pp = float(np.prod([l['p'] for l in pl['legs']])) * pl['corr']
        mk = pl['legs'][0]['market']
        v = versions['goal'] if mk == 'Goal' else versions['point'] if mk == '1+ point' else versions['sog']
        pl.update({'p': round(pp, 4), 'fair_odds': fair(pp), 'model': v, 'result': 'Pending'})
    return out

def parlay_label(pl): return ' + '.join(f"{l['player']} ({l['team']}) {l['market']}" for l in pl['legs'])

def parlay_summary(parlays):
    rows = {}
    for r in parlays:
        if not r['result'].startswith(('Won', 'Lost')): continue
        s = rows.setdefault(r['type'], {'parlay': r['type'], 'graded': 0, 'won': 0, 'expected': 0.0})
        s['graded'] += 1; s['won'] += int(r['result'].startswith('Won')); s['expected'] += r.get('p_graded', r['p'])
    order = [p[0] for p in PTYPES] + [TEAMMATES]
    return [{**rows[k], 'expected': round(rows[k]['expected'], 2), 'win_rate': round(rows[k]['won'] / rows[k]['graded'], 3)} for k in order if k in rows]

# ---------------------------------------------------------------- 5. projection
def norm(s): return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
def fair(p): return f"+{round(100 * (1 - p) / p)}" if p < 0.5 else f"-{round(100 * p / (1 - p))}"

def project(champs, models, PG, tg, gg, lg, run_date, goalies, W, days_ahead=3):
    j = get_json(f'{NHL}/schedule/{(date.fromisoformat(run_date) - timedelta(days=1)).isoformat()}', f'{W}/sched.json')
    sched = [(wk['date'], g['awayTeam']['abbrev'], g['homeTeam']['abbrev'], g['id']) for wk in j['gameWeek'] for g in wk['games']
             if g.get('gameType') == 2]
    tdates = {}
    for d, aw, hm, _ in sched: tdates.setdefault(aw, set()).add(d); tdates.setdefault(hm, set()).add(d)
    def rest(t, d):
        prev = [x for x in tdates.get(t, ()) if x < d]
        return min(7, (date.fromisoformat(d) - date.fromisoformat(max(prev))).days) if prev else 7
    slate = [(date.fromisoformat(run_date) + timedelta(days=i)).isoformat() for i in range(days_ahead + 1)]
    tgs = tg.sort_values(['defTeam', 'date'])
    lastew = lambda col, hl, base: tgs.groupby('defTeam')[col].apply(lambda s: s.ewm(halflife=hl).mean().iloc[-1] / base).to_dict()
    oppf = {'opp_xga_rel20': lastew('xGA', 20, lg['xGA']), 'opp_xga_rel10': lastew('xGA', 10, lg['xGA']),
            'opp_sa_rel20': lastew('SA', 20, lg['SA']), 'opp_sa_rel10': lastew('SA', 10, lg['SA'])}
    gq = gg.groupby('goalieIdForShot').agg(GA=('GA', 'sum'), xGA=('xGA', 'sum'), name=('name', 'last')).reset_index()
    gq['fac'] = (gq.GA + 40) / (gq.xGA + 40); gfac = gq.set_index('goalieIdForShot').fac.to_dict()
    cur = tg[tg.gkey.str.startswith(f'{CUR}_')]
    starts = cur.groupby(['defTeam', 'starterId', 'starterName']).size().reset_index(name='n').sort_values(['defTeam', 'n'], ascending=[True, False])
    assigned = {}
    def starter(team, d):
        c = starts[starts.defTeam == team]
        if d == run_date and team in goalies:
            m = c[c.starterName.map(norm).str.contains(norm(goalies[team])[:5], regex=False)]
            if len(m): assigned[(team, d)] = m.iloc[0].starterId; return m.iloc[0].starterId, m.iloc[0].starterName, True
        top = c.head(2)
        if not len(top): return None, '', False
        prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
        if prev in tdates.get(team, ()) and len(top) > 1:
            used = assigned.get((team, prev), top.iloc[0].starterId); r = top[top.starterId != used].iloc[0]
        else: r = top.iloc[0]
        assigned[(team, d)] = r.starterId; return r.starterId, r.starterName, False
    pc = PG[PG.season == CUR]
    last2 = {t: sorted(g.date.unique())[-2:] for t, g in pc.groupby('playerTeam')}
    latest = pc.sort_values('date').groupby('playerId').tail(1)
    pool = latest[[r.date in last2.get(r.playerTeam, []) for r in latest.itertuples()]]
    out = []
    for d in slate:
        rows = []
        for gd, aw, hm, gid in sched:
            if gd != d: continue
            for team, opp, ha in ((aw, hm, 'AWAY'), (hm, aw, 'HOME')):
                sid, sname, conf = starter(opp, d)
                for p in pool[pool.playerTeam == team].itertuples():
                    rows.append(dict(playerId=p.playerId, season=CUR, name=p.name, gameId=-1 - len(rows), playerTeam=team, opposingTeam=opp,
                                     home_or_away=ha, gameDate=int(d.replace('-', '')), position=p.position, situation='all',
                                     date=pd.Timestamp(d), gname=sname, gconf=conf, gfacv=gfac.get(sid, 1.0)))
        if not rows: continue
        fut = pd.DataFrame(rows)
        allp = player_feats(pd.concat([PG[PG.playerId.isin(fut.playerId)], fut], ignore_index=True))
        f = allp[allp.gameId < 0].copy()
        for c, mp in oppf.items(): f[c] = f.opposingTeam.map(mp).fillna(1)
        f['gk_fac'] = f.gfacv
        f['b2b'] = [int(rest(t, d) == 1) for t in f.playerTeam]; f['opp_b2b'] = [int(rest(t, d) == 1) for t in f.opposingTeam]
        f['rest'] = [rest(t, d) for t in f.playerTeam]
        f = f[f.n_prior >= 5].copy()
        f['p_goal'] = prob(champs['goal'], models['goal'], f, 'goal')
        f['p_point'] = prob(champs['point'], models['point'], f, 'point')
        f['sog_mu'] = lam(champs['sog'], models['sog'], f)
        for k in (2, 3, 4): f[f'p_sog{k}'] = p_atleast(f.sog_mu.values, k, models['sog']['phi'])
        f['slate'] = d
        out.append(f)
    return pd.concat(out) if out else pd.DataFrame()

def board(f):
    """Every projected player for one date as plain dicts (all markets)."""
    rows = []
    for r in f.itertuples():
        rows.append(dict(date=r.slate, player=r.name, playerId=int(r.playerId), team=r.playerTeam,
                         opp=('vs ' if r.home == 1 else '@ ') + r.opposingTeam, p_goal=round(float(r.p_goal), 3),
                         p_point=round(float(r.p_point), 3), sog_proj=round(float(r.sog_mu), 2), p_sog2=round(float(r.p_sog2), 3),
                         p_sog3=round(float(r.p_sog3), 3), p_sog4=round(float(r.p_sog4), 3),
                         toi=round(float(r.toi_e10), 1), pp_toi=round(float(r.pp_toi_e10), 1), xg60=round(float(r.xg60_e40), 2),
                         sog=round(float(r.sog_e10), 1), pts_pg=round(float(r.pts_e40), 2), finishing=round(float(r.fin25), 2),
                         due=round(float(r.due), 2), opp_goalie=r.gname + ('' if r.gconf else ' *')))
    return rows

def fix_names(rows, W):
    """MoneyPuck drops accented letters (e.g. 'Tim Sttzle'); use NHL.com roster names for display."""
    names = {}
    for t in sorted({r['team'] for r in rows}):
        try:
            j = get_json(f'{NHL}/roster/{t}/{CUR}{CUR + 1}', f'{W}/roster_{t}.json')
        except Exception:
            continue
        for grp in ('forwards', 'defensemen', 'goalies'):
            for p in j.get(grp, []):
                names[int(p['id'])] = f"{p['firstName']['default']} {p['lastName']['default']}"
    for r in rows: r['player'] = names.get(int(r['playerId']), r['player'])
    return rows

def goal_view(rows, n):
    keep = ('date', 'player', 'playerId', 'team', 'opp', 'p_goal', 'toi', 'pp_toi', 'xg60', 'sog', 'finishing', 'due', 'opp_goalie')
    return [{k: r[k] for k in keep} | {'fair_odds': fair(r['p_goal'])} for r in sorted(rows, key=lambda r: -r['p_goal'])[:n]]

def point_view(rows, n):
    keep = ('date', 'player', 'playerId', 'team', 'opp', 'p_point', 'pts_pg', 'toi', 'pp_toi', 'opp_goalie')
    return [{k: r[k] for k in keep} | {'fair_odds': fair(r['p_point'])} for r in sorted(rows, key=lambda r: -r['p_point'])[:n]]

def sog_view(rows, n):
    keep = ('date', 'player', 'playerId', 'team', 'opp', 'sog_proj', 'p_sog2', 'p_sog3', 'p_sog4', 'sog', 'toi')
    return [{k: r[k] for k in keep} | {'fair2': fair(r['p_sog2']), 'fair3': fair(r['p_sog3']), 'fair4': fair(r['p_sog4'])}
            for r in sorted(rows, key=lambda r: -r['p_sog3'])[:n]]

# ---------------------------------------------------------------- Supabase (HomerunIQ /nhl page)
def sb_get(url, key, table, select='*', order=None):
    """Read every row of a public-read table via PostgREST (paged)."""
    rows, off, page = [], 0, 1000
    hdr = ['-H', f'apikey: {key}'] + ([] if key.startswith('sb_') else ['-H', f'Authorization: Bearer {key}'])
    while True:
        q = f'{url}/rest/v1/{table}?select={select}' + (f'&order={order}' if order else '') + f'&limit={page}&offset={off}'
        r = subprocess.run(['curl', '-s', '-m', '60', '-w', '\n%{http_code}'] + hdr + [q], capture_output=True, text=True)
        body, code = r.stdout.rsplit('\n', 1)
        if code != '200': raise RuntimeError(f'Supabase read {table}: HTTP {code} {body[:200]}')
        chunk = json.loads(body); rows += chunk
        if len(chunk) < page: return rows
        off += page

def load_state_supabase(url, key):
    picks = sb_get(url, key, 'nhl_props_picks', order='pick_date.asc,market.asc,rank.asc')
    pars = sb_get(url, key, 'nhl_props_parlays', order='parlay_date.asc')
    logs = sb_get(url, key, 'nhl_props_model_log', order='id.asc')
    champ = sb_get(url, key, 'nhl_props_champion')
    num = lambda v: None if v is None else float(v)
    tracker = [{'date': r['pick_date'], 'market': r['market'], 'rank': r['rank'], 'player': r['player'], 'playerId': r['player_id'],
                'team': r['team'], 'opp': r['opp'], 'p': float(r['p']), 'fair_odds': r['fair_odds'], 'model': r['model'],
                'result': r['result'], **({'stat': r['stat']} if r['stat'] is not None else {})} for r in picks if r['result'] != 'Replaced']
    parlays = [{'date': r['parlay_date'], 'type': r['type'], 'legs': r['legs'], 'legs_n': r['legs_n'], 'corr': float(r['corr']),
                'p': float(r['p']), 'fair_odds': r['fair_odds'], 'model': r['model'], 'result': r['result'],
                **({'p_graded': float(r['p_graded'])} if r['p_graded'] is not None else {})} for r in pars]
    changelog = [{'date': r['log_date'], 'kind': r['kind'], 'market': r['market'], 'idea': r['idea'], 'champion': r['champion'],
                  'champ_A': num(r['champ_a']), 'chal_A': num(r['chal_a']), 'champ_B': num(r['champ_b']), 'chal_B': num(r['chal_b']),
                  'result': r['result']} for r in logs]
    champs = {r['market']: r['config'] for r in champ}
    return champs, changelog, tracker, parlays

def dq(obj):
    """JSON as a dollar-quoted SQL literal."""
    s = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    assert '$nhl$' not in s
    return f'$nhl${s}$nhl$::jsonb'

def write_supabase_sql(out, RD, tracker, parlays, changelog, champs, snapshots, window_days=10):
    since = (date.fromisoformat(RD) - timedelta(days=window_days)).isoformat()
    picks = [{'pick_date': r['date'], 'market': r['market'], 'player_id': int(r['playerId']), 'rank': r['rank'], 'player': r['player'],
              'team': r['team'], 'opp': r['opp'], 'p': r['p'], 'fair_odds': r['fair_odds'], 'model': r['model'], 'result': r['result'],
              'stat': r.get('stat')} for r in tracker if r['date'] >= since]
    pars = [{'parlay_date': r['date'], 'type': r['type'], 'legs': r['legs'], 'legs_n': r['legs_n'], 'corr': r.get('corr', 1.0), 'p': r['p'],
             'p_graded': r.get('p_graded'), 'fair_odds': r['fair_odds'], 'model': r['model'], 'result': r['result']} for r in parlays if r['date'] >= since]
    logs = [{'log_date': c['date'], 'kind': c['kind'], 'market': c['market'], 'idea': c['idea'], 'champion': c.get('champion'),
             'champ_a': c.get('champ_A'), 'chal_a': c.get('chal_A'), 'champ_b': c.get('champ_B'), 'chal_b': c.get('chal_B'),
             'result': c.get('result')} for c in changelog if c['date'] >= since]
    keep = [{'market': r['market'], 'player_id': int(r['playerId'])} for r in tracker if r['date'] == RD]
    state = f"""-- NHL props daily write for {RD} (idempotent upserts; no deletes)
UPDATE public.nhl_props_picks t SET result = 'Replaced', updated_at = now()
WHERE t.pick_date = '{RD}' AND t.result = 'Pending' AND NOT EXISTS (
  SELECT 1 FROM jsonb_to_recordset({dq(keep)}) AS k(market text, player_id bigint) WHERE k.market = t.market AND k.player_id = t.player_id);
INSERT INTO public.nhl_props_picks (pick_date, market, player_id, rank, player, team, opp, p, fair_odds, model, result, stat)
SELECT * FROM jsonb_to_recordset({dq(picks)}) AS x(pick_date date, market text, player_id bigint, rank int, player text, team text, opp text,
  p numeric, fair_odds text, model text, result text, stat int)
ON CONFLICT (pick_date, market, player_id) DO UPDATE SET rank = EXCLUDED.rank, p = EXCLUDED.p, fair_odds = EXCLUDED.fair_odds,
  model = EXCLUDED.model, result = EXCLUDED.result, stat = EXCLUDED.stat, opp = EXCLUDED.opp, updated_at = now();
INSERT INTO public.nhl_props_parlays (parlay_date, type, legs, legs_n, corr, p, p_graded, fair_odds, model, result)
SELECT * FROM jsonb_to_recordset({dq(pars)}) AS x(parlay_date date, type text, legs jsonb, legs_n int, corr numeric, p numeric,
  p_graded numeric, fair_odds text, model text, result text)
ON CONFLICT (parlay_date, type) DO UPDATE SET legs = EXCLUDED.legs, legs_n = EXCLUDED.legs_n, corr = EXCLUDED.corr, p = EXCLUDED.p,
  p_graded = EXCLUDED.p_graded, fair_odds = EXCLUDED.fair_odds, model = EXCLUDED.model, result = EXCLUDED.result, updated_at = now();
INSERT INTO public.nhl_props_model_log (log_date, kind, market, idea, champion, champ_a, chal_a, champ_b, chal_b, result)
SELECT * FROM jsonb_to_recordset({dq(logs)}) AS x(log_date date, kind text, market text, idea text, champion text, champ_a numeric,
  chal_a numeric, champ_b numeric, chal_b numeric, result text)
ON CONFLICT (log_date, kind, market, idea) DO UPDATE SET champion = EXCLUDED.champion, champ_a = EXCLUDED.champ_a, chal_a = EXCLUDED.chal_a,
  champ_b = EXCLUDED.champ_b, chal_b = EXCLUDED.chal_b, result = EXCLUDED.result;
INSERT INTO public.nhl_props_champion (market, config)
SELECT key, value FROM jsonb_each({dq(champs)})
ON CONFLICT (market) DO UPDATE SET config = EXCLUDED.config, updated_at = now();
"""
    open(f'{out}/supabase_state.sql', 'w').write(state)
    snap = f"-- NHL props board snapshots for {RD}\n" + ''.join(
        f"INSERT INTO public.nhl_props_snapshots (key, run_date, data) VALUES ('{k}', '{RD}', {dq(v)})\n"
        f"ON CONFLICT (key) DO UPDATE SET run_date = EXCLUDED.run_date, data = EXCLUDED.data, updated_at = now();\n"
        for k, v in snapshots.items())
    open(f'{out}/supabase_snapshots.sql', 'w').write(snap)

# ---------------------------------------------------------------- main
def migrate(champ, changelog, tracker, parlays):
    if 'terms' in champ: champ = {'goal': champ}
    for k, v in CHAMPIONS0.items(): champ.setdefault(k, v)
    for c in changelog: c.setdefault('market', 'goal')
    for r in tracker:
        r.setdefault('market', 'Goal'); r.setdefault('p', r.get('p_goal'))
        r['result'] = {'Scored': 'Hit', 'No goal': 'Miss'}.get(r['result'], r['result'])
    for r in parlays:
        r['type'] = OLD_TYPES.get(r['type'], r['type'])
        for l in r['legs']: l.setdefault('market', 'Goal')
    return champ, changelog, tracker, parlays

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-date', required=True); ap.add_argument('--state', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--work', required=True); ap.add_argument('--goalies'); ap.add_argument('--no-tests', action='store_true')
    ap.add_argument('--skip-download', action='store_true'); ap.add_argument('--track', type=int, default=10)
    ap.add_argument('--supabase-url', default='https://lfpfdpfktvjpnjwmegyd.supabase.co'); ap.add_argument('--supabase-key')
    A = ap.parse_args(); RD = A.run_date; os.makedirs(A.out, exist_ok=True); os.makedirs(A.work, exist_ok=True)
    if A.supabase_key:   # state lives in Supabase; --state is only a fallback for a first, empty database
        champs, changelog, tracker, parlays = load_state_supabase(A.supabase_url, A.supabase_key)
        log('state from Supabase:', len(tracker), 'picks', len(parlays), 'parlays', len(changelog), 'log rows', list(champs))
        if not champs and A.state and os.path.exists(f'{A.state}/champion.json'):
            champs = json.load(open(f'{A.state}/champion.json'))
        champs, changelog, tracker, parlays = migrate(champs, changelog, tracker, parlays)
    else:
        rd = lambda n, dflt: json.load(open(f'{A.state}/{n}')) if os.path.exists(f'{A.state}/{n}') else dflt
        champs, changelog, tracker, parlays = migrate(rd('champion.json', {}), rd('changelog.json', []), rd('tracker.json', []), rd('parlays.json', []))
    goalies = json.load(open(A.goalies)) if A.goalies and os.path.exists(A.goalies) else {}
    summary = {'run_date': RD, 'notes': []}

    if not A.skip_download: download(A.work)
    PG, tg, gg = build_tables(A.work)
    log('player-games', len(PG), 'latest data', PG.date.max().date())
    summary['data_through'] = str(PG.date.max().date())
    tgf, ggf, lg = team_goalie_feats(tg, gg)
    F = attach_opp(player_feats(PG), tgf)
    F['y'] = (F.g > 0).astype(int)
    F = F[(F.season >= 2024) & (F.n_prior >= 5)]

    # grade
    before = sum(r['result'] == 'Pending' for r in tracker)
    tracker = grade(tracker, RD, A.work); parlays = grade_parlays(parlays, RD, A.work)
    summary['graded'] = before - sum(r['result'] == 'Pending' for r in tracker)
    yd = (date.fromisoformat(RD) - timedelta(days=1)).isoformat()
    summary['yesterday'] = {}
    for mk in ('Goal', '1+ point', '3+ SOG'):
        y = [r for r in tracker if r['date'] == yd and r['market'] == mk and r['result'] in ('Hit', 'Miss')]
        if y: summary['yesterday'][mk] = {'picks': len(y), 'hits': sum(r['result'] == 'Hit' for r in y),
                                          'expected': round(sum(r['p'] for r in y), 2), 'hit': [r['player'] for r in y if r['result'] == 'Hit']}
    summary['yesterday_parlays'] = [f"{r['type']}: {r['result']}" for r in parlays if r['date'] == yd]

    # champion vs challengers, per market
    Wd = windows(F); summary['model_changes'] = []
    for mk in MARKETS:
        champ = champs[mk]
        n = 0 if A.no_tests else CHALLENGERS[mk]
        if not n: continue
        base = score(champ, Wd, mk)
        tried = {c['idea'] for c in changelog if c.get('kind') == 'test' and c['market'] == mk}
        queue = [i for i in IDEAS[mk] if i[0] not in tried and apply_idea(champ, i, mk) is not None]
        rnd = random.Random(RD + mk)
        while len(queue) < n:
            t = rnd.choice(sorted(TERMS))
            idea = (f'Drop {t}', 'drop', [t]) if t in champ['terms'] else (f'Add {t}', 'add', [t])
            if apply_idea(champ, idea, mk) is not None and idea[0] not in [q[0] for q in queue]: queue.append(idea)
        best = None
        for idea in queue[:n]:
            cfg = apply_idea(champ, idea, mk); s = score(cfg, Wd, mk)
            gain = {k: base[k] - s[k] for k in s}; ok = all(g >= 0.0002 for g in gain.values())
            changelog.append({'date': RD, 'kind': 'test', 'market': mk, 'idea': idea[0], 'champion': champ['version'],
                              'champ_A': round(base['A'], 5), 'chal_A': round(s['A'], 5), 'champ_B': round(base['B'], 5), 'chal_B': round(s['B'], 5),
                              'result': 'Beat champion' if ok else 'No improvement'})
            log(mk, 'idea', idea[0], gain, ok)
            if ok and (best is None or sum(gain.values()) > best[0]): best = (sum(gain.values()), idea, cfg)
        if best:
            newv = bump(champ['version']); best[2]['version'] = newv; champs[mk] = best[2]
            changelog.append({'date': RD, 'kind': 'adopt', 'market': mk, 'idea': best[1][0], 'champion': newv, 'result': f'Adopted as {newv}'})
            summary['model_changes'].append(f"{MARKETS[mk][2]}: adopted '{best[1][0]}' ({newv})")
    summary['ideas_tested'] = [f"{MARKETS[c['market']][2]} — {c['idea']}: {c['result']}" for c in changelog if c['date'] == RD and c['kind'] == 'test']

    # accuracy on window B (champion vs a recent-form baseline), calibration + top picks + parlay backtest on window A
    trB, teB = Wd['B']; trA, teA = Wd['A']; acc, top = [], []
    base_cols = {'goal': 'g_e40', 'point': 'pts_e40', 'sog': 'sog_e10'}
    pA = {}
    for mk, (tgt, k, lab) in MARKETS.items():
        te = teB[teB[tgt].notna()]; yb = ybin(te, mk)
        pm = prob(champs[mk], fit(champs[mk], trB, mk), te, mk)
        pb = np.clip(p_atleast(te[base_cols[mk]].fillna(0).values, k, 1.0), 1e-4, 1 - 1e-4)
        for name, p in (('Recent form only', pb), (f"Current model ({champs[mk]['version']})", pm)):
            acc.append({'key': f'{lab} — {name}', 'market': lab, 'model': name, 'log_loss': round(float(log_loss(yb, p)), 5),
                        'auc': round(float(roc_auc_score(yb, p)), 4), 'brier': round(float(brier_score_loss(yb, p)), 5),
                        'avg_pred_pct': round(100 * float(p.mean()), 1), 'actual_pct': round(100 * float(yb.mean()), 1), 'games': int(len(te))})
        mA = fit(champs[mk], trA, mk)
        if mk == 'sog':
            mu = lam(champs[mk], mA, teA)
            pA['p_sog2'] = p_atleast(mu, 2, mA['phi']); pA['p_sog3'] = p_atleast(mu, 3, mA['phi'])
        else: pA[f'p_{mk}'] = prob(champs[mk], mA, teA, mk)
    teA = teA.assign(**pA)
    teA['band'] = pd.cut(teA.p_goal, [0, .05, .1, .15, .2, .25, .3, .35, .4, .45, 1])
    cal = [{'band': (f"{round(b.left*100)}–{round(b.right*100)}%" if b.right < 1 else f"{round(b.left*100)}%+"),
            'predicted_pct': round(100 * g.p_goal.mean(), 1), 'actual_pct': round(100 * g.y.mean(), 1), 'n': int(len(g))}
           for b, g in teA.groupby('band', observed=True)]
    for mk, pcol, hit in (('Goal', 'p_goal', teA.g >= 1), ('1+ point', 'p_point', teA.pts >= 1), ('3+ SOG', 'p_sog3', teA.sog >= 3)):
        rk = teA.groupby('date')[pcol].rank(ascending=False, method='first')
        for kk in (1, 3, 5, 10):
            sel = rk <= kk
            top.append({'key': f'{mk} top {kk}', 'market': mk, 'picks': f'Top {kk} per night', 'predicted_pct': round(100 * teA[pcol][sel].mean(), 1),
                        'actual_pct': round(100 * hit[sel].mean(), 1), 'picks_n': int(sel.sum())})
    parlay_bt, corr = parlay_backtest(teA)

    # fit champions on everything and project
    models = {mk: fit(champs[mk], F, mk) for mk in MARKETS}
    summary['sog_dispersion'] = round(models['sog']['phi'], 3)
    P = project(champs, models, PG, tgf, ggf, lg, RD, goalies, A.work)
    brd = fix_names(board(P[P.slate == RD]), A.work) if len(P) else []
    today, today_pts, today_sog = goal_view(brd, 25), point_view(brd, 20), sog_view(brd, 20)
    upcoming = []
    for d in sorted(set(P.slate)) if len(P) else []:
        if d != RD: upcoming += goal_view(board(P[P.slate == d]), 15)
    versions = {mk: champs[mk]['version'] for mk in MARKETS}
    # log today's top picks per market (replace any earlier log for today — e.g. a re-run with confirmed goalies)
    tracker = [r for r in tracker if not (r['date'] == RD and r['result'] == 'Pending')]
    for mk, view, pk, ver in (('Goal', today, 'p_goal', versions['goal']), ('1+ point', today_pts, 'p_point', versions['point']),
                              ('3+ SOG', today_sog, 'p_sog3', versions['sog'])):
        for i, r in enumerate(view[:A.track]):
            tracker.append({'date': RD, 'market': mk, 'rank': i + 1, 'player': r['player'], 'playerId': r['playerId'], 'team': r['team'],
                            'opp': r['opp'], 'p': r[pk], 'fair_odds': fair(r[pk]), 'model': ver, 'result': 'Pending'})
    parlays = [r for r in parlays if not (r['date'] == RD and r['result'] == 'Pending')] + build_parlays(brd, RD, versions, corr)
    psum, msum, daily = parlay_summary(parlays), market_summary(tracker), daily_summary(tracker)
    summary['market_record'] = msum
    summary['today_games'] = int(P[P.slate == RD].playerTeam.nunique() // 2) if len(P) else 0
    summary['today_top'] = {'Goal': [f"{r['player']} {r['opp']} {round(r['p_goal']*100)}% ({r['fair_odds']})" for r in today[:5]],
                            '1+ point': [f"{r['player']} {r['opp']} {round(r['p_point']*100)}% ({r['fair_odds']})" for r in today_pts[:5]],
                            '3+ SOG': [f"{r['player']} {r['opp']} {round(r['p_sog3']*100)}% ({r['fair3']})" for r in today_sog[:5]]}
    summary['today_parlays'] = [f"{r['type']}: {parlay_label(r)} — {round(r['p']*100, 1)}% ({r['fair_odds']})" for r in parlays if r['date'] == RD]
    summary['parlay_record'] = psum
    summary['champions'] = versions
    if not today: summary['notes'].append('No games today')
    if len(goalies) == 0: summary['notes'].append('No confirmed goalie list supplied; all goalies are guesses')

    W = lambda n, o: json.dump(o, open(f'{A.out}/{n}', 'w'), ensure_ascii=False)
    tracker_view = [{k: r[k] for k in ('date', 'market', 'rank', 'player', 'team', 'opp', 'p', 'fair_odds', 'model', 'result')} |
                    {'key': f"{r['date']} {r['market']} {r['player']}"}
                    for r in sorted(tracker, key=lambda r: (r['date'], r['market'], -r['rank']), reverse=True)]
    outs = {
        'today.json': today, 'today_pts.json': today_pts, 'today_sog.json': today_sog,
        'upcoming.json': [u | {'key': f"{u['date']} {u['player']}"} for u in upcoming],
        'tracker_view.json': tracker_view, 'daily.json': daily, 'msum.json': msum, 'acc.json': acc, 'cal.json': cal, 'top.json': top,
        'changelog_view.json': [c | {'key': f"{c['date']} {c['market']} {c['idea']}", 'market_label': MARKETS[c['market']][2]} for c in reversed(changelog)],
        'summary.json': summary,
        'parlays_today.json': [{'key': r['type'], 'type': r['type'], 'parlay': parlay_label(r),
                                'leg_probs': ' · '.join(f"{round(l['p']*100)}%" for l in r['legs']), 'p_parlay': r['p'],
                                'fair_odds': r['fair_odds']} for r in parlays if r['date'] == RD],
        'parlay_log.json': [{'key': f"{r['date']} {r['type']}", 'date': r['date'], 'type': r['type'], 'parlay': parlay_label(r),
                             'p_parlay': r['p'], 'fair_odds': r['fair_odds'], 'model': r['model'], 'result': r['result']}
                            for r in sorted(parlays, key=lambda r: r['date'], reverse=True)],
        'parlay_summary.json': psum, 'parlay_bt.json': parlay_bt,
    }
    for n, o in outs.items(): W(n, o)
    W('tracker.json', tracker); W('champion.json', champs); W('changelog.json', changelog); W('parlays.json', parlays)
    write_supabase_sql(A.out, RD, tracker, parlays, changelog, champs,
                       {'today': today, 'today_pts': today_pts, 'today_sog': today_sog, 'msum': msum, 'parlay_summary': psum,
                        'parlay_bt': parlay_bt, 'top': top, 'summary': {k: summary[k] for k in ('run_date', 'data_through', 'champions', 'notes')}})
    log('done', json.dumps(summary, indent=1))

if __name__ == '__main__':
    main()
