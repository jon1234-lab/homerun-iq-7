"""GoalIQ web app: pages, Whop sign-in, and paywalled data APIs.

Free: today's top 3 anytime-goal picks, the full track record, and every past date in full.
Pro ($14.99/mo on Whop): full boards for goals, points and shots, all Best Bets, and parlays.
Gating happens here on the server: locked rows are never sent to the browser.
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

app = FastAPI(title="GoalIQ")
app.add_middleware(GZipMiddleware, minimum_size=1000)
HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo("America/New_York")
FREE_TOP = 3
SECURE = os.environ.get("PUBLIC_URL", "").startswith("https://")


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


def _json(obj, private=True):
    return JSONResponse(obj, headers={"Cache-Control": "private, no-store" if private else "public, max-age=60"})


def _cookie(resp, name, value, max_age):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=SECURE, samesite="lax", path="/")


# ------------------------------------------------------------------ pages + auth
@app.get("/health")
def health():
    return {"ok": True, "db": store.enabled(), "whop": auth.configured()}


@app.get("/auth/login")
def login(next: str = "/"):
    if not auth.configured():
        return HTMLResponse("Sign-in isn't set up yet.", status_code=503)
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
    except ValueError as e:
        return RedirectResponse(f"/?signin=failed&reason={str(e)[:80]}", status_code=302)
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
    resp = RedirectResponse("/", status_code=302)
    _cookie(resp, auth.SESSION_COOKIE, c, 7 * 86400)
    return resp


@app.get("/api/me")
def me(request: Request):
    v = auth.viewer(request.cookies)
    return _json(v | {"checkout_url": auth.CHECKOUT_URL, "signin_ready": auth.configured(), "price": "$14.99/mo"})


# ------------------------------------------------------------------ data
def _gate_rows(rows, pro, past, keep, lock_fields):
    """Free viewers on a current date see the first `keep` rows; the rest become empty placeholders."""
    if pro or past:
        return rows, 0
    shown = rows[:keep]
    return shown, max(len(rows) - keep, 0)


@app.get("/api/board")
def api_board(request: Request, date: str | None = None, market: str = "goal"):
    date = _date(date)
    if market not in ("goal", "point", "sog"):
        raise HTTPException(400, "market must be goal, point or sog")
    v = auth.viewer(request.cookies)
    past = is_past(date)
    b = B.build(date)
    key = {"goal": "p_goal", "point": "p_point", "sog": "p_sog3"}[market]
    rows = sorted([p for p in b["players"] if p.get(key) is not None], key=lambda p: -p[key])
    keep = FREE_TOP if market == "goal" else 0
    shown, locked = _gate_rows(rows, v["pro"], past, keep, ())
    return _json({**{k: b[k] for k in ("date", "games", "source", "generated_utc", "model", "lineups_note")},
                  "market": market, "players": shown, "locked": locked, "pro": v["pro"], "past": past})


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
        out[mk], locked[mk] = _gate_rows(rs, v["pro"], past, keep, ())
    return _json({"date": date, "markets": out, "locked": locked, "pro": v["pro"], "past": past})


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


@app.get("/api/record")
def api_record():
    with _rec_lock:
        hit = _rec_cache.get("r")
        if hit and time.time() - hit[0] < 600:
            return _json(hit[1], private=False)
    try:
        rec = store.record()
        snaps = store.snapshots()
    except Exception:
        raise HTTPException(503, "The track record is temporarily unavailable.")
    out = B.summarize_record(rec, snaps)
    with _rec_lock:
        _rec_cache["r"] = (time.time(), out)
    return _json(out, private=False)


@app.get("/api/live")
def api_live(date: str | None = None):
    date = _date(date)
    games = [g for g in D.schedule(date) if g["state"] not in B.PREGAME]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda g: (g, D.boxscore_stats(g["game_id"])), games))
    return _json({"date": date, "games": [{"game_id": g["game_id"], "state": st, "stats": stats} for g, (st, stats) in res]},
                 private=False)


# ------------------------------------------------------------------ app shell (client-side routes)
INDEX = os.path.join(HERE, "static", "index.html")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(os.path.join(HERE, "static", "favicon.svg"), media_type="image/svg+xml")


@app.get("/")
@app.get("/best-bets")
@app.get("/parlays")
@app.get("/track-record")
@app.get("/how-it-works")
@app.get("/pricing")
@app.get("/terms")
def shell():
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})
