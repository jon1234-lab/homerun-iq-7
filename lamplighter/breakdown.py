"""The full write-up behind one pick: summary, recent form and hit rates, role, matchup, history and risks.

Sources: NHL.com game logs (api-web.nhle.com), NHL team and goalie stats (api.nhle.com / api-web.nhle.com),
and the model's own inputs for tonight (the board snapshot). Each source is optional: if one is down,
that part of the write-up is left out rather than the whole thing failing.
"""
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests

import nhl_data as D
import vsopp
import why as W

WEB, STATS = "https://api-web.nhle.com/v1", "https://api.nhle.com/stats/rest/en"
_s = requests.Session()
_s.headers["User-Agent"] = "Lamplighter/1.0"
_cache, _lock = {}, threading.Lock()


def _cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def _get(url):
    r = _s.get(url, timeout=20, allow_redirects=True)
    r.raise_for_status()
    return r.json()


def _n(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def _ord(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _most(r, word="most"):
    return f"the {word}" if r == 1 else f"the {_ord(r)} {word}"


def _sv(x):
    return f"{x:.3f}".lstrip("0") if x is not None else ""


def _mins(t):
    try:
        m, s = str(t).split(":")
        return int(m) + int(s) / 60
    except Exception:
        return None


def _mmss(m):
    if m is None:
        return ""
    s = int(round(m * 60))
    return f"{s // 60}:{s % 60:02d}"


def fair(p):
    if not p or p <= 0 or p >= 1:
        return ""
    return f"-{round(100 * p / (1 - p))}" if p >= .5 else f"+{round(100 * (1 - p) / p)}"


def season_id(date):
    y = vsopp._season_start(date)
    return f"{y}{y + 1}"


# ------------------------------------------------------------------ data
def game_log(pid):
    def f():
        d = _get(f"{WEB}/player/{pid}/game-log/now")
        out = []
        for g in d.get("gameLog") or []:
            out.append({"date": g.get("gameDate"), "opp": g.get("opponentAbbrev"), "home": g.get("homeRoadFlag") == "H",
                        "g": g.get("goals") or 0, "a": g.get("assists") or 0, "pts": g.get("points") or 0,
                        "sog": g.get("shots") or 0, "ppg": g.get("powerPlayGoals") or 0, "ppp": g.get("powerPlayPoints") or 0,
                        "toi": _mins(g.get("toi"))})
        return sorted(out, key=lambda x: x["date"] or "", reverse=True)   # newest first
    return _cached(("log", pid), 1800, f)


def teams(season):
    """{abbr: goals/shots against per game, PK%, with league ranks (1 = allows the most)} plus league save %."""
    def f():
        rows = _get(f"{STATS}/team/summary?cayenneExp={quote(f'seasonId={season} and gameTypeId=2')}").get("data") or []
        st = _get(f"{WEB}/standings/now").get("standings") or []
        abbr = {_n((t.get("teamName") or {}).get("default")): (t.get("teamAbbrev") or {}).get("default") for t in st}
        out = {}
        for r in rows:
            a = abbr.get(_n(r.get("teamFullName")))
            if a and r.get("gamesPlayed"):
                out[a] = {"name": r.get("teamFullName"), "gp": r["gamesPlayed"], "ga": r.get("goalsAgainstPerGame"),
                          "sa": r.get("shotsAgainstPerGame"), "pk": r.get("penaltyKillPct"), "gf": r.get("goalsForPerGame")}
        for k, rev in (("ga", True), ("sa", True), ("pk", False)):
            order = sorted((t for t in out if out[t].get(k) is not None), key=lambda t: out[t][k], reverse=rev)
            for i, t in enumerate(order):
                out[t][f"{k}_rank"] = i + 1
        ga = sum(r["ga"] * r["gp"] for r in out.values() if r.get("ga") and r.get("sa"))
        sa = sum(r["sa"] * r["gp"] for r in out.values() if r.get("ga") and r.get("sa"))
        return {"teams": out, "league_sv": (1 - ga / sa) if sa else None, "n": len(out)}
    return _cached(("teams", season), 3 * 3600, f)


def goalie(team, name):
    if not team or not name:
        return None

    def f():
        last = _n(name.split()[-1])
        g = [p for p in D.roster(team) if p.get("pos") == "G" and _n(p["name"]).split()[-1:] == [last]]
        if not g:
            return None
        d = _get(f"{WEB}/player/{g[0]['id']}/landing")
        ss = ((d.get("featuredStats") or {}).get("regularSeason") or {}).get("subSeason") or {}
        return {"name": g[0]["name"], "gp": ss.get("gamesPlayed"), "sv": ss.get("savePctg"), "gaa": ss.get("goalsAgainstAvg"),
                "w": ss.get("wins"), "l": ss.get("losses"), "otl": ss.get("otLosses"), "so": ss.get("shutouts")}
    return _cached(("goalie", team, _n(name)), 3 * 3600, f)


def history(team, opp, pid, date):
    def f():
        rows = vsopp._team_rows(team, opp, {pid: {}}, vsopp._season_floor(date, "since2010"))
        return rows.get(pid)
    return _cached(("vs", pid, opp, vsopp._season_start(date)), 6 * 3600, f)


# ------------------------------------------------------------------ write-up
LINE = {"f1": "first line", "f2": "second line", "f3": "third line", "f4": "fourth line",
        "d1": "top defense pair", "d2": "second defense pair", "d3": "third defense pair"}
PP = {"pp1": "first power-play unit", "pp2": "second power-play unit"}


def _rate(games, hit):
    return sum(1 for g in games if hit(g)), len(games)


def _fmt_rate(h, n):
    return f"{h} of {n}" if n else "–"


def build(date, pid, market, k, built, wrow, slate):
    """built: tonight's board rows (site format). wrow: the model inputs for this player (why.norm)."""
    me = next((r for r in built if int(r["player_id"]) == pid), None)
    if not me:
        return None
    team, opp, home, name = me["team"], me["opp"], bool(me.get("home")), me["name"]
    last = name.split()[-1]
    pkey = {"goal": "p_goal", "point": "p_point"}.get(market, f"p_sog{k}")
    p = me.get(pkey)
    label = {"goal": "Anytime goal", "point": "1+ point"}.get(market, f"{k}+ shots on goal")
    verb = {"goal": "score", "point": "record a point"}.get(market, f"put {k}+ shots on goal")

    # gather the outside data in parallel; any piece may come back empty
    season = season_id(date)
    goalie_name = (me.get("opp_goalie") or "").rstrip(" *").strip()
    jobs = {"log": lambda: game_log(pid), "teams": lambda: teams(season),
            "goalie": lambda: goalie(opp, goalie_name), "vs": lambda: history(team, opp, pid, date)}
    got = {}
    with ThreadPoolExecutor(4) as ex:
        futs = {k2: ex.submit(fn) for k2, fn in jobs.items()}
        for k2, fu in futs.items():
            try:
                got[k2] = fu.result(timeout=25)
            except Exception:
                got[k2] = None
    log = got["log"] or []
    tm = (got["teams"] or {}).get("teams") or {}
    lsv = (got["teams"] or {}).get("league_sv")
    gl, vs = got["goalie"], got["vs"]
    o = tm.get(opp) or {}

    hit = {"goal": lambda g: g["g"] >= 1, "point": lambda g: g["pts"] >= 1}.get(market, lambda g: g["sog"] >= k)
    l5, l10 = log[:5], log[:10]
    rates = []
    main_lbl = {"goal": "Scored a goal", "point": "Recorded a point"}.get(market, f"{k}+ shots on goal")
    rates.append({"label": main_lbl, "l5": _fmt_rate(*_rate(l5, hit)), "l10": _fmt_rate(*_rate(l10, hit)), "season": _fmt_rate(*_rate(log, hit))})
    if market == "goal":
        rates.append({"label": "3+ shots on goal", "l5": _fmt_rate(*_rate(l5, lambda g: g["sog"] >= 3)),
                      "l10": _fmt_rate(*_rate(l10, lambda g: g["sog"] >= 3)), "season": _fmt_rate(*_rate(log, lambda g: g["sog"] >= 3))})
    elif market == "point":
        rates.append({"label": "2+ points", "l5": _fmt_rate(*_rate(l5, lambda g: g["pts"] >= 2)),
                      "l10": _fmt_rate(*_rate(l10, lambda g: g["pts"] >= 2)), "season": _fmt_rate(*_rate(log, lambda g: g["pts"] >= 2))})
    else:
        k2 = k + 1
        rates.append({"label": f"{k2}+ shots on goal", "l5": _fmt_rate(*_rate(l5, lambda g: g["sog"] >= k2)),
                      "l10": _fmt_rate(*_rate(l10, lambda g: g["sog"] >= k2)), "season": _fmt_rate(*_rate(log, lambda g: g["sog"] >= k2))})

    def tot(gs, key):
        return sum(g[key] for g in gs)

    def avg(gs, key):
        v = [g[key] for g in gs if g.get(key) is not None]
        return sum(v) / len(v) if v else None

    games = [{"date": g["date"], "opp": g["opp"], "home": g["home"], "g": g["g"], "a": g["a"], "sog": g["sog"],
              "toi": _mmss(g["toi"]), "hit": bool(hit(g)),
              "show": g["g"] if market == "goal" else (g["pts"] if market == "point" else g["sog"])} for g in reversed(l10)]
    statline = lambda gs: {"gp": len(gs), "g": tot(gs, "g"), "a": tot(gs, "a"), "pts": tot(gs, "pts"), "sog": tot(gs, "sog"),
                           "sog_pg": round(avg(gs, "sog") or 0, 1), "toi": _mmss(avg(gs, "toi")), "ppp": tot(gs, "ppp"),
                           "sh": (f"{100 * tot(gs, 'g') / tot(gs, 'sog'):.1f}%" if tot(gs, "sog") else "–")}
    form = {"l10": statline(l10), "season": statline(log)} if log else None

    # streaks
    def run(f):
        n = 0
        for g in log:
            if not f(g):
                break
            n += 1
        return n
    streak = run(hit)
    drought = run(lambda g: not hit(g))

    # slate rank for this market
    vals = sorted((r.get(pkey) for r in built if r.get(pkey) is not None), reverse=True)
    rank = vals.index(p) + 1 if p in vals else None

    # ---------- sections
    sec = []
    num = [f"The model gives {last} a {p * 100:.1f}% chance to {verb} tonight, which is fair odds of {fair(p)}."]
    if rank:
        num.append(f"That's the top number of all {len(vals)} skaters on tonight's slate." if rank == 1
                   else f"That ranks {_ord(rank)} of {len(vals)} skaters on tonight's slate for this market.")
    num.append(f"If your sportsbook pays better than {fair(p)}, the model sees value. At {fair(p)} or worse, it doesn't.")
    sec.append({"title": "The number", "items": num})

    fm = []
    if log:
        s10, ss = form["l10"], form["season"]
        fm.append(f"Last {s10['gp']} games: {s10['g']} G, {s10['a']} A, {s10['pts']} P, {s10['sog']} shots ({s10['sog_pg']} a game), {s10['toi']} average ice time.")
        fm.append(f"This season: {ss['g']} G, {ss['a']} A, {ss['pts']} P in {ss['gp']} games, {ss['sog']} shots ({ss['sog_pg']} a game), shooting {ss['sh']}, {ss['ppp']} power-play points.")
        if streak >= 3:
            fm.append({"goal": f"He's scored in {streak} straight games.", "point": f"He's on a {streak}-game point streak."}
                      .get(market, f"He's had {k}+ shots in {streak} straight games."))
        elif drought >= 5:
            fm.append({"goal": f"He hasn't scored in {drought} straight games.", "point": f"He's gone {drought} straight games without a point."}
                      .get(market, f"He's been under {k} shots in {drought} straight games."))
        t5, ts = avg(l5, "toi"), avg(log, "toi")
        if t5 and ts and t5 - ts >= 1.5:
            fm.append(f"His ice time is up lately: {_mmss(t5)} over his last 5 games versus {_mmss(ts)} for the season.")
        elif t5 and ts and ts - t5 >= 1.5:
            fm.append(f"His ice time is down lately: {_mmss(t5)} over his last 5 games versus {_mmss(ts)} for the season.")
    if fm:
        sec.append({"title": "Recent form", "items": fm})

    role = []
    ln, pu = (me.get("line") or "").lower(), (me.get("pp_unit") or "").lower()
    mates = [r["name"] for r in built if r["team"] == team and r is not me and ln and (r.get("line") or "").lower() == ln]
    ppm = [r["name"] for r in built if r["team"] == team and r is not me and pu and (r.get("pp_unit") or "").lower() == pu]
    if ln in LINE:
        role.append(f"Projected on the {LINE[ln]}" + (f" with {' and '.join(mates[:2])}." if mates else "."))
    if pu in PP:
        role.append(f"On the {PP[pu]}" + (f" with {', '.join(ppm[:4])}." if ppm else "."))
    if wrow and wrow.get("toi") is not None:
        role.append(f"The model expects about {_mmss(wrow['toi'])} of ice time" +
                    (f", {_mmss(wrow['pp_toi'])} of it on the power play." if wrow.get("pp_toi") else "."))
    if me.get("gtd"):
        role.append("Listed as a game-time decision. If he doesn't play, most books void the bet.")
    if role:
        sec.append({"title": "His role tonight", "items": role})

    mt = []
    if o:
        n_t = len(tm)
        soft = lambda r: "one of the softest in the league" if r <= 8 else ("one of the toughest in the league" if r >= n_t - 7 else "")
        ga_r = o.get("ga_rank")
        said_name = False
        if market in ("goal", "point") and ga_r:
            mt.append(f"The {o['name']} allow {o['ga']:.2f} goals a game, {_most(ga_r)} of {n_t} teams" + (f", {soft(ga_r)}." if soft(ga_r) else "."))
            said_name = True
        if o.get("sa_rank"):
            who = "They" if said_name else f"The {o['name']}"
            mt.append(f"{who} allow {o['sa']:.1f} shots a game, {_most(o['sa_rank'])} in the NHL.")
        if market in ("goal", "point") and o.get("pk_rank") and pu in PP:
            mt.append(f"Their penalty kill is at {o['pk'] * 100:.1f}%, {_most(o['pk_rank'], 'worst')} in the league, which matters for a power-play regular.")
    if gl and gl.get("sv") is not None and market in ("goal", "point"):
        status = "is projected to start" if (me.get("opp_goalie_status") == "Model guess") else "starts"
        rec = f"{gl.get('w') or 0}-{gl.get('l') or 0}-{gl.get('otl') or 0}"
        cmp = ""
        if lsv:
            diff = (gl["sv"] - lsv) * 1000
            cmp = f", {abs(diff):.0f} points {'below' if diff < 0 else 'above'} the league average of {_sv(lsv)}" if abs(diff) >= 3 else ", about league average"
        mt.append(f"{gl['name']} {status} for {opp}: {_sv(gl['sv'])} save percentage and {gl['gaa']:.2f} goals-against average in {gl['gp']} games ({rec}){cmp}.")
    gk = (wrow or {}).get("gk")
    if gk and market in ("goal", "point") and abs(gk - 1) >= .03 and goalie_name:
        mt.append(f"By the model's measure, {goalie_name} has allowed {abs(round((gk - 1) * 100))}% {'more' if gk > 1 else 'fewer'} goals than the shots he's faced would predict.")
    if (wrow or {}).get("opp_b2b"):
        mt.append(f"{opp} played last night, so they're on the second half of a back-to-back.")
    mt.append(f"The {(tm.get(team) or {}).get('name') or team} {'are at home' if home else 'are on the road'} tonight.")
    sec.append({"title": f"The matchup vs {opp}", "items": mt})

    if vs and vs.get("gp"):
        hv = [f"Since 2010-11 against {opp}: {vs['gp']} games, {vs['g']} G, {vs['a']} A, {vs['pts']} P, {vs['sog']} shots ({vs['sog'] / vs['gp']:.1f} a game)."]
        hv.append("That's a small sample, and in our testing history against one team didn't predict goals, so treat it as context.")
        sec.append({"title": f"History vs {opp}", "items": hv})

    wr = W.reasons(wrow, market if market in W.MARKETS else "sog", slate, pp_unit=me.get("pp_unit"))
    if wr["for"]:
        sec.append({"title": "What drives the model's number", "items": wr["for"]})

    watch = list(wr["against"])
    if market != "sog" and drought >= 8:
        watch.append(f"He's gone {drought} straight games without hitting this.")
    if o and o.get("ga_rank") and o["ga_rank"] >= len(tm) - 5 and market in ("goal", "point"):
        watch.append(f"{opp} are one of the stingiest teams in the league ({o['ga']:.2f} goals allowed a game).")
    if gl and gl.get("sv") and lsv and gl["sv"] - lsv >= .008 and market in ("goal", "point"):
        watch.append(f"{gl['name']} has been sharp this season ({_sv(gl['sv'])} save percentage).")
    if me.get("gtd"):
        watch.append("Game-time decision: check that he's in the lineup before you bet.")
    watch = list(dict.fromkeys(watch))[:3]

    # ---------- summary paragraph
    s = []
    rk = (", the best on tonight's slate" if rank == 1 else f", {_ord(rank)} on tonight's slate") if rank else ""
    s.append(f"{name} is a {p * 100:.1f}% chance to {verb} tonight (fair odds {fair(p)}{rk}).")
    drivers = []
    if wrow:
        r1, pct1, _ = slate.rank("sog_proj", wrow.get("sog_proj"), wrow.get("pos") == "D")
        if wrow.get("sog_proj") is not None and pct1 >= .8:
            drivers.append(f"he projects for {wrow['sog_proj']:.1f} shots, {'the most' if r1 == 1 else _ord(r1) + ' most'} on the slate" if r1 and r1 <= 10 else f"he projects for {wrow['sog_proj']:.1f} shots")
        if pu == "pp1" or (wrow.get("pp_toi") or 0) >= 2.8:
            drivers.append(f"he runs the top power play ({_mmss(wrow.get('pp_toi'))} a night)")
        if market == "goal" and wrow.get("xg60") and slate.rank("xg60", wrow["xg60"], wrow.get("pos") == "D")[1] >= .85:
            drivers.append(f"he creates {wrow['xg60']:.2f} expected goals per 60 minutes")
    if log:
        h10, n10 = _rate(l10, hit)
        if n10 >= 5 and h10 / n10 >= .5:
            what = {"goal": "scored", "point": "had a point", "sog": f"had {k}+ shots"}.get(market, "hit")
            drivers.append(f"he's {what} in all of his last {n10} games" if h10 == n10 else f"he's {what} in {h10} of his last {n10} games")
    if drivers:
        s.append(("The case: " + "; ".join(drivers[:3]) + ".").replace("The case: he", "The case: He"))
    mbits = []
    if o.get("ga_rank") and market in ("goal", "point") and o["ga_rank"] <= 10:
        mbits.append(f"{opp} allow {_most(o['ga_rank'])} goals in the league")
    if gl and gl.get("sv") and lsv and lsv - gl["sv"] >= .005 and market in ("goal", "point"):
        mbits.append(f"{gl['name']} is saving just {_sv(gl['sv'])}")
    if o.get("sa_rank") and market == "sog" and o["sa_rank"] <= 10:
        mbits.append(f"{opp} give up {_most(o['sa_rank'])} shots in the league")
    if mbits:
        s.append("The matchup helps: " + ", and ".join(mbits) + ".")
    if watch:
        s.append("The main risk: " + watch[0])
    return {"player_id": pid, "name": name, "team": team, "opp": opp, "home": home, "j": me.get("j"), "pos": me.get("pos"),
            "market": market, "k": k, "label": label, "p": p, "fair": fair(p), "rank": rank, "of": len(vals),
            "summary": " ".join(s), "games": games, "rates": rates, "form": form, "sections": sec, "watch": watch,
            "sources": "NHL.com game logs and team stats, plus the model's inputs for tonight."}
