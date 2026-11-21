"""Lamplighter: NHL player-prop picks with a Whop paywall.

Free: tonight's top 3 anytime-goal picks, every graded pick since launch, and every past slate in full.
Members (Whop day pass or monthly): tonight's full boards for goals, points and shots, parlays, and every skater.
Gating happens on the server: locked rows are never sent to the browser.

Runs on Render (uvicorn main:app) or Vercel (zero-config FastAPI). Static files live in public/.
"""
import datetime as dt
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import auth
import board as B
import nhl_data as D
import store
from page import HTML

app = FastAPI(title="Lamplighter", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
ET = ZoneInfo("America/New_York")
FREE_TOP = 3
SECURE = os.environ.get("PUBLIC_URL", "").startswith("https://")

PLANS = {
    "monthly_url": os.environ.get("WHOP_CHECKOUT_URL", ""),
    "daypass_url": os.environ.get("WHOP_DAYPASS_URL", ""),
    "monthly_price": os.environ.get("PRICE_MONTHLY", "$14.99"),
    "daypass_price": os.environ.get("PRICE_DAYPASS", "$4.99"),
    "x_handle": os.environ.get("X_HANDLE", ""),
}


def et_today():
    return dt.datetime.now(ET).date().isoformat()


def is_past(date):
    """A date's picks become public at 6am Eastern the next morning (matches the database rule)."""
    return date < (dt.datetime.now(ET) - dt.timedelta(hours=6)).date().isoformat()


def _date(d):
    d = d or et_today()
    try:
        dt.date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    return d


def _json(obj, private=True, max_age=60):
    cc = "private, no-store" if private else f"public, max-age={max_age}, s-maxage={max_age}"
    return JSONResponse(obj, headers={"Cache-Control": cc})


def _cookie(resp, name, value, max_age):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=SECURE, samesite="lax", path="/")


# ------------------------------------------------------------------ pages + auth
@app.get("/health")
def health():
    return {"ok": True, "db": store.enabled(), "whop": auth.configured(),
            "checkout": bool(PLANS["monthly_url"] or PLANS["daypass_url"])}


@app.get("/auth/login")
def login(next: str = "/"):
    if not auth.configured():
        return RedirectResponse("/?signin=soon#pricing", status_code=302)
    url, flow = auth.start_login(next)
    resp = RedirectResponse(url, status_code=302)
    _cookie(resp, auth.FLOW_COOKIE, flow, 900)
    return resp


@app.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/?signin=cancelled", status_code=302)
    try:
        session, nxt = auth.finish_login(code, state, request.cookies.get(auth.FLOW_COOKIE))
    except ValueError:
        return RedirectResponse("/?signin=failed", status_code=302)
    except Exception:
        return RedirectResponse("/?signin=failed", status_code=302)
    resp = RedirectResponse(nxt or "/", status_code=302)
    _cookie(resp, auth.SESSION_COOKIE, session, auth.SESSION_DAYS * 86400)
    resp.delete_cookie(auth.FLOW_COOKIE, path="/")
    return resp


@app.get("/auth/logout")
def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.get("/auth/preview")
def preview(key: str = ""):
    c = auth.preview_cookie(key)
    if not c:
        raise HTTPException(404)
    resp = RedirectResponse("/#board", status_code=302)
    _cookie(resp, auth.SESSION_COOKIE, c, 7 * 86400)
    return resp


@app.get("/api/me")
def me(request: Request):
    v = auth.viewer(request.cookies)
    return _json(v | PLANS | {"signin_ready": auth.configured(), "today": et_today()})


# ------------------------------------------------------------------ data
def _gate(rows, pro, past, keep):
    """Free viewers on a current date see the first `keep` rows; the rest are only counted."""
    if pro or past:
        return rows, 0
    return rows[:keep], max(len(rows) - keep, 0)


_games_cache, _games_lock = {}, threading.Lock()


@app.get("/api/games")
def api_games(date: str | None = None):
    date = _date(date)
    with _games_lock:
        hit = _games_cache.get(date)
        if hit and time.time() - hit[0] < 300:
            return _json(hit[1], private=False)
    try:
        games = D.schedule(date)
    except Exception:
        raise HTTPException(503, "The NHL schedule is temporarily unavailable.")
    try:
        sg = D.starting_goalies(date) if games else {}
    except Exception:
        sg = {}
    gl = lambda t: {"name": (sg.get(t) or {}).get("name"), "status": (sg.get(t) or {}).get("status")}
    out = {"date": date, "games": [{"away": g["away"], "home": g["home"], "start_utc": g["start_utc"], "state": g["state"],
                                    "goalies": {g["away"]: gl(g["away"]), g["home"]: gl(g["home"])}} for g in games]}
    with _games_lock:
        _games_cache[date] = (time.time(), out)
    return _json(out, private=False)


@app.get("/api/board")
def api_board(request: Request, date: str | None = None, market: str = "goal"):
    date = _date(date)
    if market not in ("goal", "point", "sog"):
        raise HTTPException(400, "market must be goal, point or sog")
    v = auth.viewer(request.cookies)
    past = is_past(date)
    if not (v["pro"] or past):   # the every-skater board is members-only on a live date
        return _json({"date": date, "market": market, "players": [], "locked": 1, "pro": False, "past": False})
    try:
        b = B.build(date)
    except Exception:
        raise HTTPException(503, "Tonight's board is temporarily unavailable. Try again in a minute.")
    key = {"goal": "p_goal", "point": "p_point", "sog": "p_sog3"}[market]
    rows = sorted([p for p in b["players"] if p.get(key) is not None], key=lambda p: -p[key])
    for r in rows:
        r.pop("headshot", None)
    return _json({**{k: b.get(k) for k in ("date", "games", "source", "generated_utc", "model", "lineups_note", "data_through")},
                  "market": market, "players": rows, "locked": 0, "pro": v["pro"], "past": past})


@app.get("/api/picks")
def api_picks(request: Request, date: str | None = None):
    date = _date(date)
    v = auth.viewer(request.cookies)
    past = is_past(date)
    try:
        rows = store.picks(date)
    except Exception:
        raise HTTPException(503, "Picks are temporarily unavailable.")
    out, locked = {}, {}
    for mk in ("Goal", "1+ point", "3+ SOG"):
        rs = sorted([r for r in rows if r["market"] == mk], key=lambda r: r["rank"])
        for r in rs:
            r["p"] = float(r["p"])
        keep = FREE_TOP if mk == "Goal" else 0
        out[mk], locked[mk] = _gate(rs, v["pro"], past, keep)
    stats = _snapshot_stats(date)
    priced = len(stats)
    for rs in out.values():   # only rows this viewer can already see get their supporting numbers
        for r in rs:
            st = stats.get(int(r["player_id"]))
            if st:
                r["stats"] = st
    return _json({"date": date, "markets": out, "locked": locked, "pro": v["pro"], "past": past, "priced": priced})


def _snapshot_stats(date):
    """Per-player numbers from the model's board snapshot for `date` (fast: one cached read, no lineup fetches)."""
    try:
        rows, _ = B._props_board(date)
    except Exception:
        return {}
    keep = ("pos", "sog_proj", "toi", "pp_toi", "xg60", "opp_goalie", "p_point", "p_sog2", "p_sog3")
    return {int(r["playerId"]): {k: r.get(k) for k in keep} for r in (rows or [])}


@app.get("/api/parlays")
def api_parlays(request: Request, date: str | None = None):
    date = _date(date)
    v = auth.viewer(request.cookies)
    past = is_past(date)
    try:
        rows = store.parlays(date)
    except Exception:
        raise HTTPException(503, "Parlays are temporarily unavailable.")
    for r in rows:
        r["p"] = float(r["p"])
    if v["pro"] or past:
        return _json({"date": date, "parlays": rows, "locked": 0, "pro": v["pro"], "past": past})
    teaser = [{"type": r["type"], "legs_n": r["legs_n"]} for r in rows]
    return _json({"date": date, "parlays": [], "locked": len(rows), "teaser": teaser, "pro": False, "past": past})


_rec_cache, _rec_lock = {}, threading.Lock()


def _cached(key, fn, ttl):
    with _rec_lock:
        hit = _rec_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _rec_lock:
        _rec_cache[key] = (time.time(), val)
    return val


@app.get("/api/record")
def api_record():
    try:
        out = _cached("record", lambda: B.summarize_record(store.record(), store.snapshots()), 600)
    except Exception:
        raise HTTPException(503, "The track record is temporarily unavailable.")
    return _json(out, private=False, max_age=300)


@app.get("/api/history")
def api_history():
    """Every graded pick (public once its night is over), with names, for the lamp wall."""
    try:
        rows = _cached("history", store.history, 600)
    except Exception:
        raise HTTPException(503, "The track record is temporarily unavailable.")
    return _json({"picks": rows}, private=False, max_age=300)


@app.get("/api/live")
def api_live(date: str | None = None):
    date = _date(date)
    games = [g for g in D.schedule(date) if g["state"] not in B.PREGAME]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda g: (g, D.boxscore_stats(g["game_id"])), games))
    return _json({"date": date, "games": [{"game_id": g["game_id"], "state": st, "stats": stats} for g, (st, stats) in res]},
                 private=False, max_age=60)


# ------------------------------------------------------------------ app shell
PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")


def _static(name, media):
    return FileResponse(os.path.join(PUBLIC, name), media_type=media, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.svg")
def favicon():
    return _static("favicon.svg", "image/svg+xml")


@app.get("/og.png")
def og():
    return _static("og.png", "image/png")


@app.get("/ice.svg")
def ice():
    return _static("ice.svg", "image/svg+xml")


@app.get("/apple-touch-icon.png")
def touch_icon():
    return _static("apple-touch-icon.png", "image/png")


@app.get("/")
@app.get("/terms")
def shell():
    return HTMLResponse(HTML, headers={"Cache-Control": "public, max-age=0, must-revalidate"})
