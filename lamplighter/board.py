"""Tonight's board: the props model's projections for every skater, merged with live lineups and goalies.

Source of probabilities: the props model's daily `board` snapshot (MoneyPuck-based, v2.x / p1.x / s1.x).
Live layer (refreshed every 10 min): DailyFaceoff lines, power-play units, game-time decisions and
confirmed starting goalies, plus NHL.com game states. Skaters who aren't in a team's projected lineup
are removed so the board never recommends someone who isn't dressing.
For dates the model hasn't run yet, the board falls back to the original GoalIQ estimate (goals only).
"""
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import nhl_data as D
import store

PREGAME = {"FUT", "PRE"}
FINAL = {"FINAL", "OFF"}
HEADSHOT = "https://assets.nhle.com/mugs/nhl/20262027/{team}/{pid}.png"
_cache, _lock = {}, threading.Lock()


def _cached(key, fn, ttl):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception:
        if hit:
            return hit[1]
        raise
    with _lock:
        _cache[key] = (now, val)
    return val


def fair(p):
    if p is None or p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def _lineups(teams):
    def one(t):
        try:
            return t, _cached(("lines", t), lambda: D.line_combos(t), 600)
        except Exception:
            return t, ({}, None)
    with ThreadPoolExecutor(8) as ex:
        return dict(ex.map(one, teams))


def _match(name, pos, combo):
    """Find a player in a DailyFaceoff lineup (handles accents, middle names, duplicate names)."""
    key = D.norm_name(name)
    c = combo.get(key)
    if c:
        return c
    parts = key.split()
    if len(parts) < 2:
        return None
    hits = [v for k, v in combo.items() if k.split()[-1] == parts[-1] and k[0] == parts[0][0]]
    if pos and len(hits) > 1:
        want_d = pos == "D"
        hits = [h for h in hits if (h["line"] or "").startswith("d") == want_d] or hits
    return hits[0] if len(hits) == 1 else None


def _props_board(date):
    snaps = store.snapshots() if store.enabled() else {}
    rows = []
    for k in sorted((k for k in snaps if k.startswith("board_")), key=lambda k: int(k.split("_")[1])):
        if snaps[k].get("run_date") == date:
            rows += snaps[k].get("data") or []
    if rows:
        return rows, (snaps.get("summary") or {}).get("data") or {}
    return None, None


def _fallback_board(date):
    import slate   # the original GoalIQ estimate: goals only
    s = slate.build(date)
    rows = []
    for p in s["players"]:
        if not p["player_id"]:
            continue
        rows.append({"playerId": p["player_id"], "player": p["name"], "team": p["team"], "pos": p["pos"],
                     "opp": ("vs " if p["home"] else "@ ") + p["opp"], "p_goal": p["p_goal"], "p_point": None,
                     "p_sog2": None, "p_sog3": None, "p_sog4": None, "sog_proj": p["exp_shots"],
                     "toi": None, "pp_toi": p["proj_pp_min"], "xg60": None, "finishing": None, "due": None,
                     "opp_goalie": ""})
    return rows


def build(date):
    return _cached(("board", date), lambda: _build(date), 120)


def _build(date):
    games = _cached(("sched", date), lambda: D.schedule(date), 60)
    if not games:
        return {"date": date, "games": [], "players": [], "source": "none", "generated_utc": None, "model": {},
                "lineups_note": ""}
    rows, summary = _props_board(date)
    source = "props"
    if rows is None:
        rows, summary, source = _fallback_board(date), {}, "early"
    teams = sorted({t for g in games for t in (g["home"], g["away"])})
    today = dt.date.fromisoformat(date) >= dt.date.today() - dt.timedelta(days=1)
    lines = _lineups(teams) if today else {}
    try:
        sg = _cached(("goalies", date), lambda: D.starting_goalies(date), 600) if today else {}
    except Exception:
        sg = {}

    by_team = {}
    for g in games:
        by_team[g["away"]] = (g, False, g["home"])
        by_team[g["home"]] = (g, True, g["away"])
    game_out = []
    for g in games:
        gl = {}
        for t in (g["away"], g["home"]):
            info = sg.get(t) or {}
            gl[t] = {"name": info.get("name"), "status": info.get("status") or ("Unconfirmed" if info.get("name") else None)}
        game_out.append({"game_id": g["game_id"], "away": g["away"], "home": g["home"], "start_utc": g["start_utc"],
                         "state": g["state"], "goalies": gl})

    out, removed = [], []
    for r in rows:
        if r["team"] not in by_team:
            continue
        g, home, opp = by_team[r["team"]]
        combo = (lines.get(r["team"]) or ({}, None))[0]
        has_lines = any(c.get("line") for c in combo.values())
        c = _match(r["player"], r.get("pos"), combo) if has_lines else None
        if has_lines and (not c or not c.get("line")):
            removed.append(r["player"])
            continue
        # the goalie the model priced in (so the number and the name always match)
        model_g = (r.get("opp_goalie") or "").strip()
        og = model_g.rstrip(" *").strip() or (sg.get(opp) or {}).get("name") or None
        og_status = "Model guess" if model_g.endswith("*") else ("Confirmed" if model_g else None)
        p_goal, p_point, p_sog3 = r.get("p_goal"), r.get("p_point"), r.get("p_sog3")
        out.append({
            "player_id": int(r["playerId"]), "name": r["player"], "team": r["team"], "opp": opp, "home": home,
            "game_id": g["game_id"], "pos": r.get("pos"),
            "headshot": HEADSHOT.format(team=r["team"], pid=int(r["playerId"])),
            "line": (c or {}).get("line"), "pp_unit": (c or {}).get("pp"), "gtd": bool((c or {}).get("gtd")),
            "opp_goalie": og, "opp_goalie_status": og_status,
            "p_goal": p_goal, "fair_goal": fair(p_goal),
            "p_point": p_point, "fair_point": fair(p_point),
            "p_sog2": r.get("p_sog2"), "p_sog3": p_sog3, "p_sog4": r.get("p_sog4"), "fair_sog3": fair(p_sog3),
            "sog_proj": r.get("sog_proj"), "toi": r.get("toi"), "pp_toi": r.get("pp_toi"), "xg60": r.get("xg60"),
            "finishing": r.get("finishing"), "due": r.get("due"),
        })
    note = ""
    if removed:
        note = f"{len(removed)} skater{'s' if len(removed) != 1 else ''} not in tonight's projected lineups removed"
    model = (summary or {}).get("champions") or {}
    return {"date": date, "games": game_out, "players": out, "source": source, "model": model,
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "lineups_note": note,
            "data_through": (summary or {}).get("data_through")}


# ------------------------------------------------------------------ track record
def summarize_record(rec, snaps):
    picks, parlays = rec.get("picks") or [], rec.get("parlays") or []
    markets = []
    for mk in ("Goal", "1+ point", "3+ SOG"):
        rs = [r for r in picks if r["m"] == mk]
        tiers = []
        for k in (1, 3, 5, 10):
            t = [r for r in rs if r["r"] <= k]
            if t:
                tiers.append({"label": f"Top {k}" if k > 1 else "Top pick", "n": len(t),
                              "hits": sum(r["res"] == "Hit" for r in t),
                              "expected": round(sum(float(r["p"]) for r in t), 1)})
        days = {}
        for r in rs:
            d = days.setdefault(r["d"], {"date": r["d"], "n": 0, "hits": 0, "expected": 0.0})
            d["n"] += 1
            d["hits"] += r["res"] == "Hit"
            d["expected"] += float(r["p"])
        series = sorted(({**v, "expected": round(v["expected"], 2)} for v in days.values()), key=lambda x: x["date"])
        markets.append({"market": mk, "graded": len(rs), "hits": sum(r["res"] == "Hit" for r in rs),
                        "expected": round(sum(float(r["p"]) for r in rs), 1), "tiers": tiers, "days": series})
    ptypes = {}
    for r in parlays:
        t = ptypes.setdefault(r["t"], {"type": r["t"], "graded": 0, "won": 0, "expected": 0.0})
        t["graded"] += 1
        t["won"] += r["res"].startswith("Won")
        t["expected"] += float(r["p"])
    pl = sorted(({**v, "expected": round(v["expected"], 2)} for v in ptypes.values()), key=lambda x: x["type"])
    first = min((r["d"] for r in picks), default=None)
    bt = {"top": (snaps.get("top") or {}).get("data") or [], "parlays": (snaps.get("parlay_bt") or {}).get("data") or []}
    return {"since": first, "markets": markets, "parlays": pl, "backtest": bt,
            "champions": ((snaps.get("summary") or {}).get("data") or {}).get("champions") or {}}
