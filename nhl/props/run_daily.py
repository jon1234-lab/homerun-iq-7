"""Daily GoalIQ job (Render cron): goalies -> models -> publish.

    GOALIQ_PIPELINE_SECRET=... python3 nhl/props/run_daily.py [--date YYYY-MM-DD]

1. Today's date in America/Denver (same convention as the picks history).
2. Starting goalies from DailyFaceoff (confirmed / likely / projected) -> goalies.json.
3. daily.py: downloads MoneyPuck data, grades earlier picks, tests model ideas, projects tonight, writes SQL.
4. publish.py: sends that SQL through the locked-down goaliq_write() function.
5. Verifies today's picks reached the database; exits non-zero (so Render flags the run) if anything failed.
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, tempfile, urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SB = os.environ.get('SUPABASE_URL', 'https://lfpfdpfktvjpnjwmegyd.supabase.co').rstrip('/')
PUB = os.environ.get('SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_DyoU7kki74m2sYKvBqBHUQ_QsXc5BF9')
SLUG = {'anaheim-ducks': 'ANA', 'boston-bruins': 'BOS', 'buffalo-sabres': 'BUF', 'calgary-flames': 'CGY', 'carolina-hurricanes': 'CAR',
        'chicago-blackhawks': 'CHI', 'colorado-avalanche': 'COL', 'columbus-blue-jackets': 'CBJ', 'dallas-stars': 'DAL',
        'detroit-red-wings': 'DET', 'edmonton-oilers': 'EDM', 'florida-panthers': 'FLA', 'los-angeles-kings': 'LAK',
        'minnesota-wild': 'MIN', 'montreal-canadiens': 'MTL', 'nashville-predators': 'NSH', 'new-jersey-devils': 'NJD',
        'new-york-islanders': 'NYI', 'new-york-rangers': 'NYR', 'ottawa-senators': 'OTT', 'philadelphia-flyers': 'PHI',
        'pittsburgh-penguins': 'PIT', 'san-jose-sharks': 'SJS', 'seattle-kraken': 'SEA', 'st-louis-blues': 'STL',
        'tampa-bay-lightning': 'TBL', 'toronto-maple-leafs': 'TOR', 'utah-mammoth': 'UTA', 'vancouver-canucks': 'VAN',
        'vegas-golden-knights': 'VGK', 'washington-capitals': 'WSH', 'winnipeg-jets': 'WPG'}


def log(*a):
    print(dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S'), *a, flush=True)


def goalies(day, path):
    out = {}
    try:
        req = urllib.request.Request(f'https://www.dailyfaceoff.com/starting-goalies/{day}', headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=40).read().decode('utf-8', 'replace')
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        for g in json.loads(m.group(1))['props']['pageProps'].get('data', []):
            for side in ('home', 'away'):
                team, name = SLUG.get(g.get(side + 'TeamSlug')), g.get(side + 'GoalieName')
                if team and name and g.get(side + 'NewsStrengthName'):     # skip unrated placeholders
                    out[team] = name.split()[-1]
    except Exception as e:
        log('goalies unavailable:', e)
    json.dump(out, open(path, 'w'))
    log('goalies', len(out), out)


def pending_today(day):
    req = urllib.request.Request(f'{SB}/rest/v1/rpc/goaliq_read', method='POST', headers={'apikey': PUB, 'Content-Type': 'application/json'},
                                 data=json.dumps({'secret': os.environ.get('GOALIQ_DB_SECRET', ''), 'what': 'picks', 'd': day}).encode())
    try:
        return len(json.loads(urllib.request.urlopen(req, timeout=60).read()))
    except Exception as e:
        log('verify skipped:', e)
        return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--date'); ap.add_argument('--skip-download', action='store_true')
    a = ap.parse_args()
    day = a.date or dt.datetime.now(ZoneInfo('America/Denver')).date().isoformat()
    base = os.environ.get('GOALIQ_WORKDIR') or tempfile.mkdtemp(prefix='goaliq_')
    for d in ('state', 'out', 'work'):
        os.makedirs(f'{base}/{d}', exist_ok=True)
    log('run', day, 'in', base)
    goalies(day, f'{base}/goalies.json')
    cmd = [sys.executable, f'{HERE}/daily.py', '--run-date', day, '--state', f'{base}/state', '--out', f'{base}/out',
           '--work', f'{base}/work', '--goalies', f'{base}/goalies.json', '--supabase-key', PUB]
    if a.skip_download:
        cmd.append('--skip-download')
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f'daily.py failed with exit code {r.returncode}')
    r = subprocess.run([sys.executable, f'{HERE}/publish.py', f'{base}/out/supabase_state.sql', f'{base}/out/supabase_snapshots.sql'])
    if r.returncode != 0:
        sys.exit('publish failed')
    s = json.load(open(f'{base}/out/summary.json'))
    n = pending_today(day)
    log('published; games tonight:', s.get('today_games'), '| picks in database for', day, ':', n)
    if s.get('today_games') and n == 0:
        sys.exit('no picks reached the database')


if __name__ == '__main__':
    main()
