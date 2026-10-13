"""Supabase persistence over PostgREST (no DB driver needed).

Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  (same values as the HomerunIQ backend)
All writes are upserts -> safe on cron retries / cold starts.
Once a game leaves pregame its predictions are frozen (locked=true) and never overwritten,
so the track record can't change after puck drop. Grading happens only on FINAL games.
"""
import datetime as dt
import os

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PREGAME = {"FUT", "PRE"}
PRED_COLS = ("game_id", "player_id", "name", "team", "opp", "home", "pos", "line", "pp_unit", "gtd",
             "p_goal", "fair_odds", "exp_shots", "sh_pct_reg", "proj_ev_min", "proj_pp_min",
             "goalie_mult", "defense_mult", "gp_sample")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def enabled():
    return bool(URL and KEY)


def _h(extra=None):
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def _upsert(table, rows, on_conflict):
    for i in range(0, len(rows), 500):
        r = requests.post(f"{URL}/rest/v1/{table}?on_conflict={on_conflict}", json=rows[i:i + 500],
                          headers=_h({"Prefer": "resolution=merge-duplicates,return=minimal"}), timeout=30)
        r.raise_for_status()


def _get(table, params):
    r = requests.get(f"{URL}/rest/v1/{table}", params=params, headers=_h(), timeout=30)
    r.raise_for_status()
    return r.json()


def _patch(table, params, body):
    r = requests.patch(f"{URL}/rest/v1/{table}", params=params, json=body,
                       headers=_h({"Prefer": "return=minimal"}), timeout=30)
    r.raise_for_status()


def save_slate(slate):
    if not enabled() or not slate["games"]:
        return {"saved": 0, "locked": 0}
    date = slate["date"]
    games = {g["game_id"]: g for g in slate["games"]}
    _upsert("nhl_games", [{
        "game_id": g["game_id"], "game_date": date, "away": g["away"], "home": g["home"],
        "start_utc": g["start_utc"], "state": g["state"],
        "away_goalie": (g["goalies"].get(g["away"]) or {}).get("name"),
        "home_goalie": (g["goalies"].get(g["home"]) or {}).get("name"), "updated_at": _now(),
    } for g in games.values()], "game_id")

    locked_ids = {r["game_id"] for r in _get("nhl_predictions", {
        "select": "game_id", "game_date": f"eq.{date}", "locked": "eq.true"})}
    rows = []
    for p in slate["players"]:
        g = games[p["game_id"]]
        if g["state"] not in PREGAME or not p["player_id"] or p["game_id"] in locked_ids:
            continue
        rows.append({k: p[k] for k in PRED_COLS} | {
            "game_date": date, "opp_goalie": (g["goalies"].get(p["opp"]) or {}).get("name"),
            "model_version": slate["model"]["version"], "updated_at": _now()})
    if rows:
        _upsert("nhl_predictions", rows, "game_id,player_id")

    started = [gid for gid, g in games.items() if g["state"] not in PREGAME]
    for gid in started:
        _patch("nhl_predictions", {"game_id": f"eq.{gid}", "locked": "eq.false"}, {"locked": True})
    return {"saved": len(rows), "locked": len(started)}


def load_slate_rows(date):
    if not enabled():
        return []
    return _get("nhl_predictions", {"select": "*", "game_date": f"eq.{date}"})


def grade(results_by_game):
    """results_by_game: {game_id: {player_id: goals}} for FINAL games only."""
    if not enabled():
        return 0
    n = 0
    for gid, scorers in results_by_game.items():
        for r in _get("nhl_predictions", {"select": "player_id", "game_id": f"eq.{gid}", "scored": "is.null"}):
            g = scorers.get(r["player_id"], 0)
            _patch("nhl_predictions", {"game_id": f"eq.{gid}", "player_id": f"eq.{r['player_id']}"},
                   {"scored": g > 0, "goals": g, "graded_at": _now(), "locked": True})
            n += 1
    return n


def track_record():
    if not enabled():
        return []
    return _get("nhl_predictions", {"select": "game_date,name,team,opp,p_goal,scored,goals",
                                    "scored": "not.is.null", "order": "game_date.desc", "limit": "50000"})
