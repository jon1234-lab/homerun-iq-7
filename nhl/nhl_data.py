"""Data access for the GoalIQ NHL goalscorer model.

Sources (all public, no key):
  - api-web.nhle.com/v1        : schedule, rosters, boxscores
  - api.nhle.com/stats/rest/en : aggregated + per-game stats with gameDate filters
      (leak-free: aggregate over any date window in ONE call -> pre-game rates)
  - dailyfaceoff.com           : projected lines, PP units, starting goalies (__NEXT_DATA__ JSON)
"""
import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
DFO = "https://www.dailyfaceoff.com"
UA = {"User-Agent": "Mozilla/5.0 (GoalIQ data fetch)"}

_session = requests.Session()
_session.headers.update(UA)

TEAM_SLUGS = {
    "ANA": "anaheim-ducks", "BOS": "boston-bruins", "BUF": "buffalo-sabres",
    "CGY": "calgary-flames", "CAR": "carolina-hurricanes", "CHI": "chicago-blackhawks",
    "COL": "colorado-avalanche", "CBJ": "columbus-blue-jackets", "DAL": "dallas-stars",
    "DET": "detroit-red-wings", "EDM": "edmonton-oilers", "FLA": "florida-panthers",
    "LAK": "los-angeles-kings", "MIN": "minnesota-wild", "MTL": "montreal-canadiens",
    "NSH": "nashville-predators", "NJD": "new-jersey-devils", "NYI": "new-york-islanders",
    "NYR": "new-york-rangers", "OTT": "ottawa-senators", "PHI": "philadelphia-flyers",
    "PIT": "pittsburgh-penguins", "SJS": "san-jose-sharks", "SEA": "seattle-kraken",
    "STL": "st-louis-blues", "TBL": "tampa-bay-lightning", "TOR": "toronto-maple-leafs",
    "UTA": "utah-mammoth", "VAN": "vancouver-canucks", "VGK": "vegas-golden-knights",
    "WSH": "washington-capitals", "WPG": "winnipeg-jets",
}
SLUG_TO_ABBR = {v: k for k, v in TEAM_SLUGS.items()}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", "", s.lower().replace("-", " "))
    return re.sub(r"\s+", " ", s).strip()


def _get_json(url, tries=3):
    for i in range(tries):
        try:
            r = _session.get(url, timeout=30, allow_redirects=True)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def _stats(report, cayenne, is_game=False, aggregate=True):
    url = (f"{STATS}/{report}?isAggregate={'true' if aggregate else 'false'}"
           f"&isGame={'true' if is_game else 'false'}&limit=-1&start=0"
           f"&cayenneExp={quote(cayenne)}")
    return _get_json(url).get("data", [])


def _window(start, end):
    return f'gameDate>="{start}" and gameDate<="{end}" and gameTypeId=2'


# ---------------------------------------------------------------- aggregates
def skaters_window(start, end):
    """Aggregated skater totals in [start, end] keyed by playerId (pre-game safe)."""
    c = _window(start, end)
    with ThreadPoolExecutor(2) as ex:
        s_f = ex.submit(_stats, "skater/summary", c)
        t_f = ex.submit(_stats, "skater/timeonice", c)
        summ, toi = s_f.result(), t_f.result()
    toi_by = {r["playerId"]: r for r in toi}
    out = {}
    for r in summ:
        t = toi_by.get(r["playerId"], {})
        gp = r.get("gamesPlayed") or 0
        out[r["playerId"]] = {
            "name": r["skaterFullName"], "pos": r["positionCode"],
            "team": (r.get("teamAbbrevs") or "").split(",")[-1].strip(),
            "gp": gp, "goals": r.get("goals") or 0, "pp_goals": r.get("ppGoals") or 0,
            "shots": r.get("shots") or 0,
            "toi": t.get("timeOnIce") or (r.get("timeOnIcePerGame") or 0) * gp,
            "pp_toi": t.get("ppTimeOnIce") or 0,
            "sh_toi": t.get("shTimeOnIce") or 0,
        }
    for v in out.values():
        v["ev_toi"] = max(v["toi"] - v["pp_toi"] - v["sh_toi"], 0)
    return out


def goalies_window(start, end):
    out = {}
    for r in _stats("goalie/summary", _window(start, end)):
        out[r["playerId"]] = {"name": r["goalieFullName"], "sa": r.get("shotsAgainst") or 0,
                              "ga": r.get("goalsAgainst") or 0, "gs": r.get("gamesStarted") or 0}
    return out


_TEAM_IDS = None


def team_id_map():
    global _TEAM_IDS
    if _TEAM_IDS is None:
        _TEAM_IDS = {r["id"]: r["triCode"] for r in _get_json(f"{STATS}/team")["data"]}
    return _TEAM_IDS


def team_sa_window(start, end):
    """({abbrev: shots_against_per_game}, league average)."""
    ids = team_id_map()
    rows = _stats("team/summary", _window(start, end), aggregate=False)
    out = {ids.get(r["teamId"]): r["shotsAgainstPerGame"] for r in rows if r.get("shotsAgainstPerGame")}
    lg = sum(out.values()) / max(len(out), 1)
    return out, lg


# ---------------------------------------------------------------- per-game rows (backtest)
def skater_games(date):
    return _stats("skater/summary", f'gameDate="{date}" and gameTypeId=2', is_game=True, aggregate=False)


def goalie_games(date):
    return _stats("goalie/summary", f'gameDate="{date}" and gameTypeId=2', is_game=True, aggregate=False)


# ---------------------------------------------------------------- live slate
def schedule(date):
    d = _get_json(f"{WEB}/schedule/{date}")
    day = next((w for w in d.get("gameWeek", []) if w["date"] == date), None)
    games = []
    for g in (day or {}).get("games", []):
        if g.get("gameType") != 2:
            continue
        games.append({"game_id": g["id"], "start_utc": g["startTimeUTC"], "state": g["gameState"],
                      "away": g["awayTeam"]["abbrev"], "home": g["homeTeam"]["abbrev"],
                      "venue": g.get("venue", {}).get("default")})
    return games


def roster(team):
    d = _get_json(f"{WEB}/roster/{team}/current")
    out = []
    for grp in ("forwards", "defensemen", "goalies"):
        for p in d.get(grp, []):
            nm = f"{p['firstName']['default']} {p['lastName']['default']}"
            out.append({"id": p["id"], "name": nm, "pos": p["positionCode"],
                        "headshot": p.get("headshot")})
    return out


def _dfo_next_data(path):
    r = _session.get(DFO + path, timeout=30)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


def starting_goalies(date=None):
    pp = _dfo_next_data("/starting-goalies" + (f"/{date}" if date else ""))
    out = {}
    for g in pp.get("data", []):
        for side in ("home", "away"):
            abbr = SLUG_TO_ABBR.get(g.get(f"{side}TeamSlug"))
            if abbr:
                out[abbr] = {"name": g.get(f"{side}GoalieName"),
                             "status": g.get(f"{side}NewsStrengthName")}
    return out


def line_combos(team):
    """{norm_name: {name, line 'f1'..'f4'|'d1'..'d3'|None, pp 'pp1'|'pp2'|None, injured, gtd}}, updatedAt."""
    pp = _dfo_next_data(f"/teams/{TEAM_SLUGS[team]}/line-combinations")
    combo = pp.get("combinations") or {}
    out = {}
    for p in combo.get("players", []):
        key = norm_name(p["name"])
        rec = out.setdefault(key, {"name": p["name"], "line": None, "pp": None,
                                   "injured": False, "gtd": False})
        grp = p.get("groupIdentifier") or ""
        if p.get("categoryIdentifier") == "ev" and re.fullmatch(r"[fd]\d", grp):
            rec["line"] = grp
        elif grp in ("pp1", "pp2"):
            rec["pp"] = rec["pp"] or grp
        if p.get("gameTimeDecision") or p.get("injuryStatus") == "dtd":
            rec["gtd"] = True
        if grp == "ir" and p.get("injuryStatus") in ("ir", "out", "ltir"):
            rec["injured"] = True
    return out, combo.get("updatedAt")


def boxscore_goals(game_id):
    """(gameState, {playerId: goals}) — works live and after FINAL."""
    d = _get_json(f"{WEB}/gamecenter/{game_id}/boxscore")
    out = {}
    for side in ("awayTeam", "homeTeam"):
        t = (d.get("playerByGameStats") or {}).get(side, {})
        for grp in ("forwards", "defense"):
            for p in t.get(grp, []):
                out[p["playerId"]] = p.get("goals") or 0
    return d.get("gameState"), out
