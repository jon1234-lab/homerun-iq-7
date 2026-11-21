"""Production vs tonight's opponent: each skater's history against the team he faces tonight.

Like Frozen Tools' "Production vs Today's Opponent", built on the NHL's public stats API.
One query per team and report: every skater on that team, games against tonight's opponent,
aggregated over the chosen window. Cached per (date, window) for a few hours.
"""
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import nhl_data as D

WINDOWS = {"season": "This season", "3y": "Last 3 seasons", "since2010": "Since 2010-11"}
TTL = 6 * 3600
_cache, _lock, _building = {}, threading.Lock(), {}


def _season_start(date):
    d = dt.date.fromisoformat(date)
    return d.year if d.month >= 9 else d.year - 1


def _season_floor(date, window):
    y = _season_start(date)
    first = {"season": y, "3y": y - 2, "since2010": 2010}[window]
    return first * 10000 + first + 1


def _query(report, cayenne):
    rows, start = [], 0
    while True:
        url = (f"{D.STATS}/skater/{report}?isAggregate=true&isGame=true&limit=100&start={start}"
               f"&cayenneExp={quote(cayenne)}")
        d = D._get_json(url)
        chunk = d.get("data", [])
        rows += chunk
        start += 100
        if len(chunk) < 100 or start >= (d.get("total") or 0):
            return rows


def _team_rows(team, opp, players, floor):
    """players: {playerId: {...}} for this team. Returns merged history rows keyed by playerId."""
    ids = ",".join(str(i) for i in players)
    c = f'gameTypeId=2 and seasonId>={floor} and opponentTeamAbbrev="{opp}" and playerId in ({ids})'
    summ, real, toi = _query("summary", c), _query("realtime", c), _query("timeonice", c)
    rb = {r["playerId"]: r for r in real}
    tb = {r["playerId"]: r for r in toi}
    out = {}
    for r in summ:
        pid, rr, tt = r["playerId"], rb.get(r["playerId"], {}), tb.get(r["playerId"], {})
        gp = r.get("gamesPlayed") or 0
        out[pid] = {"gp": gp, "g": r.get("goals") or 0, "a": r.get("assists") or 0, "pts": r.get("points") or 0,
                    "ppp": r.get("ppPoints") or 0, "sog": r.get("shots") or 0, "pim": r.get("penaltyMinutes") or 0,
                    "hit": rr.get("hits") or 0, "blk": rr.get("blockedShots") or 0,
                    "toi": round((r.get("timeOnIcePerGame") or 0) / 60, 2),
                    "pptoi": round((tt.get("ppTimeOnIcePerGame") or 0) / 60, 2),
                    "pos": r.get("positionCode")}
    return out


def build(date, window, board_rows, games):
    """board_rows: the model's snapshot rows for `date` (may be empty). games: schedule for `date`."""
    floor = _season_floor(date, window)
    opp_of = {}
    for g in games:
        opp_of[g["away"]], opp_of[g["home"]] = (g["home"], False), (g["away"], True)
    by_team = {}
    for r in board_rows or []:
        if r.get("team") in opp_of:
            by_team.setdefault(r["team"], {})[int(r["playerId"])] = r
    for t in opp_of:   # no model rows yet (before the morning run): fall back to the current roster
        if t not in by_team:
            try:
                by_team[t] = {p["id"]: {"player": p["name"], "pos": p["pos"], "team": t, "playerId": p["id"]}
                              for p in D.roster(t) if p["pos"] != "G"}
            except Exception:
                by_team[t] = {}

    def one(t):
        try:
            return t, _team_rows(t, opp_of[t][0], by_team[t], floor) if by_team[t] else {}
        except Exception:
            return t, None
    with ThreadPoolExecutor(8) as ex:
        hist = dict(ex.map(one, sorted(by_team)))
    rows, failed = [], [t for t, h in hist.items() if h is None]
    for t, players in by_team.items():
        opp, home = opp_of[t]
        for pid, b in players.items():
            h = (hist.get(t) or {}).get(pid) or {}
            pos = h.get("pos") or b.get("pos") or ""
            rows.append({"id": pid, "name": b.get("player") or b.get("name"), "team": t, "opp": opp, "home": home,
                         "pos": pos, "p_goal": b.get("p_goal"), "p_point": b.get("p_point"), "p_sog3": b.get("p_sog3"),
                         **{k: h.get(k, 0) for k in ("gp", "g", "a", "pts", "ppp", "sog", "pim", "hit", "blk")},
                         "toi": h.get("toi"), "pptoi": h.get("pptoi")})
    rows.sort(key=lambda r: (-(r["pts"] or 0), -(r["gp"] or 0)))
    return {"date": date, "window": window, "window_label": WINDOWS[window], "since_season": f"{str(floor)[:4]}-{str(floor)[6:]}",
            "rows": rows, "failed_teams": failed, "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat()}


def get(date, window, board_rows_fn, games_fn):
    key = (date, window)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
        ev = _building.get(key)
        mine = ev is None
        if mine:
            ev = _building[key] = threading.Event()
    if not mine:   # someone else is building it: wait for theirs
        ev.wait(90)
        with _lock:
            hit = _cache.get(key)
        if hit:
            return hit[1]
        raise RuntimeError("still building")
    try:
        val = build(date, window, board_rows_fn(), games_fn())
        if not val["failed_teams"]:
            with _lock:
                _cache[key] = (time.time(), val)
        return val
    finally:
        with _lock:
            _building.pop(key, None)
        ev.set()
