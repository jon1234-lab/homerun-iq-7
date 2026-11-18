"""PuckLedger web app. Free: every model's record, last night's graded bets, and all research tools.
Pro (Whop): tonight's bets from every model before puck drop, plus model probabilities and edges.
Gating happens here; locked rows never reach the browser."""
import datetime as dt
import os
import sys
import threading
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import auth  # noqa: E402
from engine import bets as B  # noqa: E402
from engine import research as R  # noqa: E402
from engine import run as RUN  # noqa: E402
from engine import store  # noqa: E402

ET = ZoneInfo("America/New_York")
PRICE = os.environ.get("PRICE_LABEL", "$19.99/mo")
SECURE = os.environ.get("PUBLIC_URL", "").startswith("https://")
app = FastAPI(title="PuckLedger")
app.add_middleware(GZipMiddleware, minimum_size=1000)
ENGINE = RUN.Engine()
_cache, _clock = {}, threading.Lock()


@app.on_event("startup")
def _start():
    if os.environ.get("ENGINE_OFF") == "1":
        return
    threading.Thread(target=ENGINE.loop, kwargs={"every": int(os.environ.get("TICK_SECONDS", "600"))},
                     daemon=True).start()


def cached(key, ttl, fn):
    now = time.time()
    with _clock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _clock:
        _cache[key] = (now, val)
    return val


def et_today():
    return dt.datetime.now(ET).date().isoformat()


def is_past(date):
    return date < (dt.datetime.now(ET) - dt.timedelta(hours=6)).date().isoformat()


def _date(d):
    d = d or et_today()
    try:
        dt.date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    return d


def _san(o):
    if isinstance(o, dict):
        return {str(k): _san(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_san(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if f != f or f in (float("inf"), float("-inf")) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if o is pd.NaT or o is pd.NA:
        return None
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


def J(obj, private=True):
    return JSONResponse(_san(obj), headers={"Cache-Control": "private, no-store" if private else "public, max-age=60"})


def ready():
    if ENGINE.sg is None:
        raise HTTPException(503, "Warming up — loading this season's games. Try again in a minute.")


def _cookie(resp, name, value, max_age):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=SECURE, samesite="lax", path="/")


# ------------------------------------------------------------------ auth
@app.get("/health")
def health():
    return {"ok": True, "db": store.enabled(), "whop": auth.configured(), "odds": ENGINE.odds.ok(),
            "data": ENGINE.sg is not None, "last_tick": ENGINE.status.get("last_tick")}


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
    resp = RedirectResponse("/tonight", status_code=302)
    _cookie(resp, auth.SESSION_COOKIE, c, 7 * 86400)
    return resp


@app.get("/api/me")
def me(request: Request):
    v = auth.viewer(request.cookies)
    return J(v | {"checkout_url": auth.CHECKOUT_URL, "signin_ready": auth.configured(), "price": PRICE})


@app.get("/admin/status")
def admin_status(request: Request):
    if not auth.viewer(request.cookies).get("preview"):
        raise HTTPException(404)
    return J(ENGINE.status | {"odds_key": ENGINE.odds.ok(), "credits": ENGINE.odds.remaining,
                              "backfill": store.get_kv("backfill").get("backfill")})


@app.post("/admin/tick")
def admin_tick(request: Request):
    if not auth.viewer(request.cookies).get("preview"):
        raise HTTPException(404)
    threading.Thread(target=ENGINE.tick, daemon=True).start()
    return {"started": True}


# ------------------------------------------------------------------ models + bets
def _clean(b):
    for k in ("line", "p_model", "ev", "units", "stat"):
        if b.get(k) is not None:
            b[k] = float(b[k])
    return b


def _bets(date):
    return cached(("bets", date), 90, lambda: [_clean(b) for b in store.get_bets(date, date)
                                              if b["model"] in B.MODELS or b["model"] in B.PARLAYS])


def _last_graded_date():
    def f():
        today = et_today()
        frm = (dt.date.fromisoformat(today) - dt.timedelta(days=10)).isoformat()
        ds = sorted({b["d"] for b in store.get_bets(frm, today) if b["result"] != "pending"})
        return ds[-1] if ds else None
    return cached("lastdate", 300, f)


@app.get("/api/models")
def api_models():
    def build():
        rec = store.get_kv("records").get("records") or {"models": [], "parlays": []}
        last = _last_graded_date()
        lb = _bets(last) if last else []
        by = {}
        for b in lb:
            by.setdefault(b["model"], []).append(b)
        nights = []
        for m, bs in by.items():
            g = [b for b in bs if b["result"] in ("win", "loss")]
            nights.append({"model": m, "parlay": m in B.PARLAYS, "w": sum(b["result"] == "win" for b in g),
                           "l": sum(b["result"] == "loss" for b in g), "units": round(sum(b["units"] or 0 for b in g), 2),
                           "bets": sorted(bs, key=lambda b: -(b["units"] or 0))})
        nights.sort(key=lambda n: -n["units"])
        meta = {m: v for m, v in B.MODELS.items()}
        return {"records": rec, "last_date": last, "last_night": nights, "meta": meta, "parlay_meta": B.PARLAYS,
                "tonight": _tonight_summary()}
    return J(cached("models", 120, build), private=False)


def _tonight_summary():
    today = et_today()
    bs = _bets(today)
    games = []
    try:
        games = ENGINE.board.get(today, {}).get("games", []) if ENGINE.sg is not None else []
    except Exception:
        pass
    nxt = None
    for g in games:
        t = dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) - RUN.POST_LEAD
        if not any(b["game_id"] == g["game_id"] for b in bs):
            nxt = min(nxt, t) if nxt else t
    return {"date": today, "posted": len([b for b in bs if b["kind"] != "parlay"]), "games": len(games),
            "next_post_utc": nxt.isoformat() if nxt else None}


@app.get("/api/bets")
def api_bets(request: Request, date: str | None = None):
    date = _date(date)
    v = auth.viewer(request.cookies)
    past = is_past(date)
    bs = _bets(date)
    if v["pro"] or past:
        return J({"date": date, "bets": bs, "locked": 0, "pro": v["pro"], "past": past})
    counts = {}
    for b in bs:
        counts[b["model"]] = counts.get(b["model"], 0) + 1
    card = sorted([b for b in bs if b["model"] == "Best Card"], key=lambda b: -b["ev"])
    free = [dict(card[-1], free=True)] if card else []
    return J({"date": date, "bets": free, "locked": len(bs) - len(free), "counts": counts, "pro": False, "past": False,
              "summary": _tonight_summary()})


# ------------------------------------------------------------------ games + props (tonight's board)
def _board(date):
    ready()
    return ENGINE.tonight(date)


def _odds_snapshot(date):
    return cached(("odds", date), 120, lambda: store.get_kv(f"odds:{date}").get(f"odds:{date}", {}))


@app.get("/api/games")
def api_games(request: Request, date: str | None = None):
    date = _date(date)
    v = auth.viewer(request.cookies)
    b = _board(date)
    od = _odds_snapshot(date)
    out = []
    for g in b["games"]:
        lines = od.get(str(g["game_id"]), {})
        row = {k: g.get(k) for k in ("game_id", "home", "away", "start_utc", "state", "home_goalie", "away_goalie",
                                     "home_status", "away_status", "home_win", "away_win", "exp_total", "home_m15",
                                     "away_m15", "home_p15", "away_p15")}
        row["over"] = g["over"]
        row["lines"] = lines
        if not (v["pro"] or is_past(date)):
            for k in ("home_m15", "away_m15", "home_p15", "away_p15"):
                row[k] = None
            row["over"] = {}
        out.append(row)
    return J({"date": date, "games": out, "pro": v["pro"]})


@app.get("/api/props")
def api_props(request: Request, date: str | None = None):
    date = _date(date)
    v = auth.viewer(request.cookies)
    pro = v["pro"] or is_past(date)

    def build():
        b = _board(date)
        P = b["players"]
        if not len(P):
            return []
        vs = ENGINE.vs_lookup(date)
        od = _odds_snapshot(date)
        rows = []
        for r in P.itertuples():
            f = ENGINE.form.get(int(r.playerId), {})
            h = vs.get((int(r.playerId), r.opp), {})
            offers = od.get("p" + str(int(r.playerId)), [])
            rows.append({"id": int(r.playerId), "name": r.name, "team": r.team, "opp": r.opp, "home": int(r.home),
                         "pos": r.pos, "line": getattr(r, "line", None), "pp": getattr(r, "pp", None),
                         "gp": f.get("gp", 0), "l5_pt": f.get("l5_pt"), "l10_pt": f.get("l10_pt"), "s_pt": f.get("s_pt"),
                         "l5_g": f.get("l5_g"), "l10_g": f.get("l10_g"), "s_g": f.get("s_g"),
                         "l10_sog3": f.get("l10_sog3"), "s_sog3": f.get("s_sog3"), "pt_streak": f.get("pt_streak", 0),
                         "vs_gp": h.get("gp", 0), "vs_pts": h.get("pts", 0),
                         "vs_pt_pct": round(h["gw_pt"] / h["gp"], 3) if h.get("gp") else None,
                         "p_point": round(float(r.p_point), 4), "p_goal": round(float(r.p_goal), 4),
                         "p_sog3": round(float(r.p_sog3), 4), "exp_sog": round(float(r.mu_shots), 2),
                         "offers": offers, "goalie": r.opp_goalie})
        rows.sort(key=lambda x: -x["p_point"])
        return rows
    rows = cached(("props", date), 300, build)
    if not pro:
        rows = [{k: v_ for k, v_ in r.items() if k not in ("p_point", "p_goal", "p_sog3", "exp_sog")} for r in rows]
    return J({"date": date, "players": rows, "pro": pro})


@app.get("/api/matchups")
def api_matchups(date: str | None = None):
    date = _date(date)

    def build():
        b = _board(date)
        P = b["players"]
        vs = ENGINE.vs_lookup(date)
        out = []
        for r in P.itertuples():
            h = vs.get((int(r.playerId), r.opp))
            if h and h["gp"] >= 5:
                out.append({"id": int(r.playerId), "name": r.name, "team": r.team, "opp": r.opp, "gp": h["gp"],
                            "pts": h["pts"], "ppg": round(h["pts"] / h["gp"], 2), "pt_pct": round(h["gw_pt"] / h["gp"], 3)})
        return sorted(out, key=lambda x: (-x["ppg"], -x["gp"]))[:40]
    return J({"date": date, "rows": cached(("matchups", date), 600, build)}, private=False)


# ------------------------------------------------------------------ research
@app.get("/api/players")
def api_players():
    ready()
    return J(cached("players", 1800, lambda: R.player_index(ENGINE.sg)), private=False)


@app.get("/api/vs/{pid}")
def api_vs(pid: int):
    ready()
    out = cached(("vs", pid), 1800, lambda: R.vs_table(pid, ENGINE.sg, ENGINE.vs_hist))
    if not out["player"]:
        raise HTTPException(404, "Player not found")
    out["names"] = cached("teamnames", 86400, lambda: R.team_names(ENGINE.tg))
    return J(out, private=False)


@app.get("/api/streaks")
def api_streaks():
    ready()
    return J(cached("streaks", 900, lambda: R.streak_lists(ENGINE.form, ENGINE.sg.date.max())), private=False)


@app.get("/api/teams")
def api_teams():
    ready()

    def build():
        totals = {}
        for k, v in store.kv_prefix("odds:").items():
            for gid, mk in v.items():
                if gid.startswith("p"):
                    continue
                lines = [o["line"] for o in mk.get("total", []) if o.get("line") is not None]
                if lines:
                    totals[int(gid)] = max(set(lines), key=lines.count)
        return R.team_trends(ENGINE.tg, totals)
    return J(cached("teams", 900, build), private=False)


# ------------------------------------------------------------------ app shell
STATIC = os.path.join(HERE, "static")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(os.path.join(STATIC, "favicon.svg"), media_type="image/svg+xml")


@app.get("/")
@app.get("/tonight")
@app.get("/games")
@app.get("/players")
@app.get("/props")
@app.get("/streaks")
@app.get("/teams")
@app.get("/how")
@app.get("/pricing")
@app.get("/terms")
def shell():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})
