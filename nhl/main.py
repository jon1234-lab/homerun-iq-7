import datetime as dt
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import db
import nhl_data as D
import slate

app = FastAPI(title="GoalIQ API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
HERE = os.path.dirname(os.path.abspath(__file__))
_cache, _lock = {}, threading.Lock()
TTL = 300
PREGAME = {"FUT", "PRE"}


def et_today():
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _cached_build(date):
    with _lock:
        hit = _cache.get(date)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    s = slate.build(date)
    with _lock:
        _cache[date] = (time.time(), s)
    return s


def _merge_locked(s):
    """For games that have started, serve the frozen pregame numbers + grades from the DB."""
    started = {g["game_id"] for g in s["games"] if g["state"] not in PREGAME}
    if not started or not db.enabled():
        return s
    try:
        rows = {(r["game_id"], r["player_id"]): r for r in db.load_slate_rows(s["date"])}
    except Exception:
        return s
    for p in s["players"]:
        r = rows.get((p["game_id"], p["player_id"]))
        if p["game_id"] in started and r:
            p["p_goal"], p["fair_odds"], p["locked"], p["scored"] = float(r["p_goal"]), r["fair_odds"], True, r.get("scored")
    s["players"].sort(key=lambda p: -p["p_goal"])
    return s


@app.get("/health")
def health():
    return {"ok": True, "db": db.enabled()}


@app.get("/api/slate")
def api_slate(date: str | None = None):
    date = date or et_today()
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    return JSONResponse(_merge_locked(_cached_build(date)), headers={"Cache-Control": "public, max-age=60"})


@app.get("/api/live")
def api_live(date: str | None = None):
    date = date or et_today()
    games = [g for g in D.schedule(date) if g["state"] not in PREGAME]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda g: (g, D.boxscore_goals(g["game_id"])), games))
    return {"date": date, "games": [
        {"game_id": g["game_id"], "state": st, "scorers": {str(k): v for k, v in goals.items() if v > 0}}
        for g, (st, goals) in res]}


@app.get("/api/track-record")
def api_track_record():
    try:
        rows = db.track_record()
    except Exception:
        rows = []
    if not rows:
        return {"n": 0, "buckets": [], "top_pick_days": 0}
    for r in rows:
        r["p_goal"] = float(r["p_goal"])
    n = len(rows)
    brier = sum((r["p_goal"] - (1 if r["scored"] else 0)) ** 2 for r in rows) / n
    edges = [0, .1, .15, .2, .25, .3, .35, 1.01]
    buckets = []
    for lo, hi in zip(edges, edges[1:]):
        b = [r for r in rows if lo <= r["p_goal"] < hi]
        if b:
            buckets.append({"range": f"{int(lo * 100)}-{min(int(hi * 100), 100)}%", "n": len(b),
                            "predicted": round(sum(r["p_goal"] for r in b) / len(b), 4),
                            "actual": round(sum(1 for r in b if r["scored"]) / len(b), 4)})
    days = {}
    for r in rows:
        days.setdefault(r["game_date"], []).append(r)
    top = [max(v, key=lambda r: r["p_goal"]) for v in days.values()]
    return {"n": n, "brier": round(brier, 5), "buckets": buckets, "top_pick_days": len(top),
            "top_pick_hits": sum(1 for r in top if r["scored"]),
            "top_pick_expected": round(sum(r["p_goal"] for r in top), 2)}


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))
