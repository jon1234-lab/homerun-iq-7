"""Data loading: static history shipped in data/, the current season pulled fresh from the NHL stats API,
and the live slate (schedule, box scores, DailyFaceoff lineups/goalies)."""
import datetime as dt
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from . import tables

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
STATS = "https://api.nhle.com/stats/rest/en"
WEB = "https://api-web.nhle.com/v1"
DFO = "https://www.dailyfaceoff.com"
CUR_SEASON = int(os.environ.get("NHL_SEASON", "20262027"))
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (PuckLedger)"})

TEAM_SLUGS = {
    "ANA": "anaheim-ducks", "BOS": "boston-bruins", "BUF": "buffalo-sabres", "CGY": "calgary-flames",
    "CAR": "carolina-hurricanes", "CHI": "chicago-blackhawks", "COL": "colorado-avalanche",
    "CBJ": "columbus-blue-jackets", "DAL": "dallas-stars", "DET": "detroit-red-wings", "EDM": "edmonton-oilers",
    "FLA": "florida-panthers", "LAK": "los-angeles-kings", "MIN": "minnesota-wild", "MTL": "montreal-canadiens",
    "NSH": "nashville-predators", "NJD": "new-jersey-devils", "NYI": "new-york-islanders", "NYR": "new-york-rangers",
    "OTT": "ottawa-senators", "PHI": "philadelphia-flyers", "PIT": "pittsburgh-penguins", "SJS": "san-jose-sharks",
    "SEA": "seattle-kraken", "STL": "st-louis-blues", "TBL": "tampa-bay-lightning", "TOR": "toronto-maple-leafs",
    "UTA": "utah-mammoth", "VAN": "vancouver-canucks", "VGK": "vegas-golden-knights", "WSH": "washington-capitals",
    "WPG": "winnipeg-jets"}
SLUG_TO_ABBR = {v: k for k, v in TEAM_SLUGS.items()}


def _get(url, tries=4, timeout=40):
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


# ------------------------------------------------------------------ season game logs
def _windows(season):
    y = int(str(season)[:4])
    d, end = dt.date(y, 9, 1), min(dt.date(y + 1, 7, 15), dt.date.today() + dt.timedelta(days=1))
    while d < end:
        e = min(d + dt.timedelta(days=14), end)
        yield d, e
        d = e


COLS = {
    "skater/summary": ["gameId", "gameDate", "playerId", "skaterFullName", "positionCode", "teamAbbrev", "opponentTeamAbbrev",
                       "homeRoad", "goals", "assists", "points", "shots", "ppPoints", "ppGoals", "timeOnIcePerGame"],
    "skater/timeonice": ["gameId", "playerId", "ppTimeOnIce", "shTimeOnIce"],
    "goalie/summary": ["gameId", "gameDate", "playerId", "goalieFullName", "teamAbbrev", "opponentTeamAbbrev",
                       "gamesStarted", "shotsAgainst", "saves", "goalsAgainst", "timeOnIce"],
    "team/summary": ["gameId", "gameDate", "teamFullName", "opponentTeamAbbrev", "homeRoad", "goalsFor", "goalsAgainst",
                     "shotsForPerGame", "shotsAgainstPerGame", "wins", "winsInRegulation", "winsInShootout", "otLosses",
                     "losses"]}


def _report(rep, season, a, b):
    rows = _report_raw(rep, season, a, b)
    return pd.DataFrame(rows, columns=COLS[rep]) if rows else pd.DataFrame(columns=COLS[rep])


def _report_raw(rep, season, a, b):
    key = "teamId" if rep.startswith("team") else "playerId"
    q = dict(isAggregate="false", isGame="true", start=0, limit=-1,
             sort=json.dumps([{"property": "gameId", "direction": "ASC"}, {"property": key, "direction": "ASC"}]),
             cayenneExp=f'seasonId={season} and gameTypeId=2 and gameDate>="{a}" and gameDate<"{b}"')
    return _get(f"{STATS}/{rep}?" + urllib.parse.urlencode(q))["data"]


def pull_season(season=CUR_SEASON):
    jobs = [(rep, w) for rep in ("skater/summary", "skater/timeonice", "goalie/summary", "team/summary")
            for w in _windows(season)]
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(lambda j: (j[0], _report(j[0], season, *j[1])), jobs))
    raw = {}
    for rep, frame in res:
        raw.setdefault(rep, []).append(frame)
    df = {k: pd.concat(v, ignore_index=True).assign(seasonId=season) for k, v in raw.items()}
    if df["skater/summary"].empty:
        return None
    return (tables.skater_table(df["skater/summary"], df["skater/timeonice"]),
            tables.team_table(df["team/summary"]), tables.goalie_table(df["goalie/summary"]))


FIRST_SEASON = int(os.environ.get("HIST_FIRST_SEASON", "20232024"))


STR_COLS = ("name", "team", "opp", "pos", "teamFullName")


def _hist(name):
    df = pd.read_pickle(os.path.join(DATA, f"{name}.pkl.gz"), compression="gzip")
    if "season" in df.columns:
        df = df[df.season >= FIRST_SEASON]
    for c in STR_COLS:
        if c in df.columns:
            df[c] = df[c].astype(object)   # shares the category strings: cheap
    return df.reset_index(drop=True)


def load_all():
    """(sg, tg, gg) = shipped history + this season so far."""
    hs, ht, hg = _hist("hist_sg"), _hist("hist_tg"), _hist("hist_gg")
    cur = pull_season()
    if cur is not None:
        sg, tg, gg = cur
        hs, ht, hg = (pd.concat([hs, sg], ignore_index=True), pd.concat([ht, tg], ignore_index=True),
                      pd.concat([hg, gg], ignore_index=True))
    return hs, ht, hg


def vs_history():
    return _hist("vs_hist")


# ------------------------------------------------------------------ live slate
def schedule(date):
    d = _get(f"{WEB}/schedule/{date}")
    day = next((w for w in d.get("gameWeek", []) if w["date"] == date), None)
    out = []
    for g in (day or {}).get("games", []):
        if g.get("gameType") != 2:
            continue
        out.append({"game_id": g["id"], "start_utc": g["startTimeUTC"], "state": g["gameState"],
                    "away": g["awayTeam"]["abbrev"], "home": g["homeTeam"]["abbrev"],
                    "away_score": g["awayTeam"].get("score"), "home_score": g["homeTeam"].get("score"),
                    "venue": (g.get("venue") or {}).get("default")})
    return out


def boxscore(game_id):
    """{'state', 'home','away','home_score','away_score', 'went_so', 'players': {pid: {goals, points, sog}}}"""
    d = _get(f"{WEB}/gamecenter/{game_id}/boxscore")
    players = {}
    for side in ("awayTeam", "homeTeam"):
        t = (d.get("playerByGameStats") or {}).get(side, {})
        for grp in ("forwards", "defense"):
            for p in t.get(grp, []):
                players[int(p["playerId"])] = {"goals": p.get("goals") or 0, "points": p.get("points") or 0,
                                               "sog": p.get("sog") or 0}
    pd_ = d.get("periodDescriptor") or {}
    return {"state": d.get("gameState"), "home": d["homeTeam"]["abbrev"], "away": d["awayTeam"]["abbrev"],
            "home_score": d["homeTeam"].get("score"), "away_score": d["awayTeam"].get("score"),
            "period_type": pd_.get("periodType"), "players": players}


def _dfo(path):
    r = S.get(DFO + path, timeout=30)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


def starting_goalies(date):
    try:
        pp = _dfo(f"/starting-goalies/{date}")
    except Exception:
        return {}
    out = {}
    for g in pp.get("data", []):
        for side in ("home", "away"):
            abbr = SLUG_TO_ABBR.get(g.get(f"{side}TeamSlug"))
            if abbr and g.get(f"{side}GoalieName"):
                out[abbr] = {"name": g.get(f"{side}GoalieName"), "status": g.get(f"{side}NewsStrengthName")}
    return out


def line_combos(team):
    """({norm_name: {name, line, pp, injured, gtd}}, updatedAt) from DailyFaceoff; ({}, None) on failure."""
    from .odds import norm
    try:
        pp = _dfo(f"/teams/{TEAM_SLUGS[team]}/line-combinations")
    except Exception:
        return {}, None
    combo = pp.get("combinations") or {}
    out = {}
    for p in combo.get("players", []):
        rec = out.setdefault(norm(p["name"]), {"name": p["name"], "line": None, "pp": None, "injured": False, "gtd": False})
        grp = p.get("groupIdentifier") or ""
        if p.get("categoryIdentifier") == "ev" and re.fullmatch(r"[fd]\d", grp):
            rec["line"] = grp
        elif grp in ("pp1", "pp2"):
            rec["pp"] = rec["pp"] or grp
        if p.get("gameTimeDecision") or p.get("injuryStatus") == "dtd":
            rec["gtd"] = True
        if grp == "ir" or p.get("injuryStatus") in ("ir", "out", "ltir"):
            rec["injured"] = True
    return out, combo.get("updatedAt")
