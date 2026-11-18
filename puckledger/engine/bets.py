"""Betting models: rules that turn projections + sportsbook prices into 1-unit bets, plus grading.

Every rule was fixed before the season replay was run; records are published whatever they show.
"""
import hashlib
import json

import numpy as np
import pandas as pd

from . import model as M
from .odds import norm

PROP_LABEL = {"goal": "Anytime goal", "points": "Points", "sog": "Shots on goal"}

MODELS = {
    "Goal Value": {"kind": "prop", "desc": "Anytime goalscorers the model prices well above the book", "tag": "Props"},
    "Point Value": {"kind": "prop", "desc": "1+ point where the model's chance beats the price", "tag": "Props"},
    "Shots Over": {"kind": "prop", "desc": "Shots-on-goal overs with a model edge", "tag": "Shots"},
    "Shots Under": {"kind": "prop", "desc": "Shots-on-goal unders with a model edge", "tag": "Shots"},
    "Matchup History": {"kind": "prop", "desc": "Players who produce against tonight's opponent (5+ games)", "tag": "Trends"},
    "Hot Hand": {"kind": "prop", "desc": "Active point streaks backed by a fair price", "tag": "Trends"},
    "Consensus": {"kind": "prop", "desc": "Props two or more models agree on", "tag": "Consensus"},
    "Moneyline Model": {"kind": "game", "desc": "Team model edges on the moneyline", "tag": "Game lines"},
    "Puck Line Model": {"kind": "game", "desc": "Team model edges on the ±1.5 puck line", "tag": "Game lines"},
    "Totals Model": {"kind": "game", "desc": "Team model edges on the game total", "tag": "Game lines"},
    "Best Card": {"kind": "mixed", "desc": "Up to 5 best bets a night, across every model", "tag": "Best bets"},
}
PARLAYS = {"Points 2-leg": "Two most likely 1+ point players, different games",
           "Points 3-leg": "Three most likely 1+ point players, different games",
           "Value 2-leg": "Two highest-edge props, different games"}


def ev(p, price):
    return p * M.american_to_decimal(price) - 1


def _key(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:20]


# ------------------------------------------------------------------ matching sportsbook names to NHL ids
ALIASES = {"mitch marner": "mitchell marner", "alex wennberg": "alexander wennberg", "matt boldy": "matthew boldy",
           "zach werenski": "zachary werenski", "josh norris": "joshua norris", "nick paul": "nicholas paul",
           "alex killorn": "alexander killorn", "cam york": "cameron york", "mike matheson": "michael matheson",
           "will smith": "william smith", "johnny gaudreau": "john gaudreau", "jj peterka": "john jason peterka",
           "tim stutzle": "tim stuetzle", "alexei toropchenko": "alexey toropchenko", "pat maroon": "patrick maroon"}


def match_players(offers, cands):
    """offers: rows with pkey/home/away; cands: DataFrame[playerId, name, team]. Adds player_id + team."""
    cands = cands.copy()
    cands["key"] = cands.name.map(norm)
    cands["last"] = cands.key.str.split().str[-1]
    cands["fi"] = cands.key.str[:1]
    out = []
    for o in offers:
        if o["market"] not in ("goal", "points", "sog"):
            out.append(o)
            continue
        pool = cands[cands.team.isin([o["home"], o["away"]])]
        k = ALIASES.get(o["pkey"], o["pkey"])
        hit = pool[pool.key == k]
        if hit.empty:
            parts = k.split()
            if parts:
                hit = pool[(pool["last"] == parts[-1]) & (pool.fi == parts[0][:1])]
                if len(hit) != 1:
                    hit = pool[pool["last"] == parts[-1]] if len(pool[pool["last"] == parts[-1]]) == 1 else hit.iloc[0:0]
        if len(hit) == 1:
            o = dict(o, player_id=int(hit.playerId.iloc[0]), team=hit.team.iloc[0], name=hit.name.iloc[0])
            out.append(o)
    return out


# ------------------------------------------------------------------ bet construction
def _prop_bet(model, date, r, o, p, source, posted, extra=None):
    side = o["side"]
    mk = o["market"]
    if mk == "goal":
        label = "Anytime goal"
    elif mk == "points":
        label = f"{'Over' if side == 'over' else 'Under'} {o['line']} points"
    else:
        label = f"{'Over' if side == 'over' else 'Under'} {o['line']} SOG"
    b = {"bet_key": _key(date, model, r["game_id"], mk, r["player_id"], side, o["line"]),
         "d": date, "game_id": int(r["game_id"]), "start_utc": r.get("start_utc"), "model": model, "kind": "prop",
         "market": mk, "bet": label, "selection": r["name"], "player_id": int(r["player_id"]), "team": r["team"],
         "opp": r["opp"], "side": side, "line": o["line"], "price": int(o["price"]), "book": o["book"],
         "p_model": round(float(p), 4), "ev": round(float(ev(p, o["price"])), 4), "source": source,
         "posted_utc": posted, "result": "pending", "units": None, "stat": None, "legs": None}
    if extra:
        b["note"] = extra
    return b


def _game_bet(model, date, g, mk, side, line, price, book, p, source, posted):
    if mk == "ml":
        label, sel = "Moneyline", side
    elif mk == "pl":
        label, sel = f"Puck line {'+' if line > 0 else ''}{line:g}", side
    else:
        label, sel = f"{'Over' if side == 'over' else 'Under'} {line:g}", f"{g['away']} @ {g['home']}"
    return {"bet_key": _key(date, model, g["game_id"], mk, side, line), "d": date, "game_id": int(g["game_id"]),
            "start_utc": g.get("start_utc"), "model": model, "kind": "game", "market": mk, "bet": label,
            "selection": sel, "player_id": None, "team": side if mk in ("ml", "pl") else g["home"],
            "opp": (g["away"] if side == g["home"] else g["home"]) if mk in ("ml", "pl") else g["away"],
            "side": side, "line": line, "price": int(price), "book": book, "p_model": round(float(p), 4),
            "ev": round(float(ev(p, price)), 4), "source": source, "posted_utc": posted, "result": "pending",
            "units": None, "stat": None, "legs": None}


def make_bets(date, players, games, offers, vs, form, source, posted):
    """players: DataFrame of tonight's projections (one row per player_id, with game_id/start_utc);
    games: list of game_table rows (+start_utc); offers: best-price rows (matched);
    vs: {(player_id, opp): {gp, pts, gw_pt}}; form: {player_id: {pt_streak, l10_pt}}."""
    P = players.set_index("playerId")
    by_model = {m: [] for m in MODELS}
    prop_cands = []
    for o in offers:
        if o["market"] not in ("goal", "points", "sog") or "player_id" not in o or o["player_id"] not in P.index:
            continue
        r = P.loc[o["player_id"]]
        r = {"game_id": r.gameId, "start_utc": r.start_utc, "player_id": o["player_id"], "name": r["name"],
             "team": r.team, "opp": r.opp, "mu_shots": r.mu_shots, "p_goal": r.p_goal, "p_point": r.p_point,
             "mu_points": r.mu_points}
        if o["market"] == "goal" and o["side"] == "yes":
            p = r["p_goal"]
        elif o["market"] == "points" and o["line"] is not None:
            pov = float(M.p_at_least(r["mu_points"], int(np.floor(o["line"])) + 1))
            p = pov if o["side"] == "over" else 1 - pov
        elif o["market"] == "sog" and o["line"] is not None:
            pov = float(M.p_over_shots(r["mu_shots"], o["line"]))
            p = pov if o["side"] == "over" else 1 - pov
        else:
            continue
        prop_cands.append((r, o, p, ev(p, o["price"])))

    def top(items, n):
        return sorted(items, key=lambda x: -x[3])[:n]

    gv = [c for c in prop_cands if c[1]["market"] == "goal" and c[3] >= 0.12 and c[2] >= 0.18 and c[1]["price"] <= 450]
    by_model["Goal Value"] = [_prop_bet("Goal Value", date, *c[:3], source, posted) for c in top(gv, 6)]
    pts_over = [c for c in prop_cands if c[1]["market"] == "points" and c[1]["side"] == "over" and c[1]["line"] == 0.5]
    pv = [c for c in pts_over if c[3] >= 0.05 and c[2] >= 0.25 and c[1]["price"] <= 300]
    by_model["Point Value"] = [_prop_bet("Point Value", date, *c[:3], source, posted) for c in top(pv, 6)]
    # one shots line per player: the line most books hang
    sog = [c for c in prop_cands if c[1]["market"] == "sog" and c[1]["line"] is not None and c[1]["line"] <= 4.5]
    best_line = {}
    for c in sog:
        k = c[0]["player_id"]
        if k not in best_line or c[1]["n_books"] > best_line[k]:
            best_line[k] = c[1]["n_books"]
    so = [c for c in sog if c[1]["side"] == "over" and c[3] >= 0.05 and c[1]["line"] <= 3.5 and c[2] >= 0.25
          and c[1]["price"] <= 300]
    su = [c for c in sog if c[1]["side"] == "under" and c[3] >= 0.05 and c[1]["price"] >= -300]
    by_model["Shots Over"] = [_prop_bet("Shots Over", date, *c[:3], source, posted) for c in _one_per_player(top(so, 40))[:6]]
    by_model["Shots Under"] = [_prop_bet("Shots Under", date, *c[:3], source, posted) for c in _one_per_player(top(su, 40))[:6]]
    mh = []
    for c in pts_over:
        h = vs.get((c[0]["player_id"], c[0]["opp"]))
        if h and h["gp"] >= 5 and h["pts"] / h["gp"] >= 0.9 and h["gw_pt"] / h["gp"] >= 0.6 and c[3] >= -0.03:
            mh.append((c, h["pts"] / h["gp"]))
    mh.sort(key=lambda x: -x[1])
    by_model["Matchup History"] = [_prop_bet("Matchup History", date, *c[:3], source, posted,
                                             f"{vs[(c[0]['player_id'], c[0]['opp'])]['pts']} pts in "
                                             f"{vs[(c[0]['player_id'], c[0]['opp'])]['gp']} GP vs {c[0]['opp']}")
                                   for c, _ in mh[:5]]
    hh = []
    for c in pts_over:
        f = form.get(c[0]["player_id"])
        if f and f["pt_streak"] >= 3 and f["l10_pt"] >= 0.7 and c[1]["price"] >= -250 and c[3] >= -0.03:
            hh.append((c, f["pt_streak"] + f["l10_pt"]))
    hh.sort(key=lambda x: -x[1])
    by_model["Hot Hand"] = [_prop_bet("Hot Hand", date, *c[:3], source, posted,
                                      f"{form[c[0]['player_id']]['pt_streak']}-game point streak") for c, _ in hh[:5]]
    # consensus: same player 1+ point picked by 2+ of the point models, or goal value + shots over on one player
    votes = {}
    for m in ("Point Value", "Matchup History", "Hot Hand"):
        for b in by_model[m]:
            votes.setdefault(b["player_id"], []).append(m)
    cons = []
    for c in pts_over:
        ms = votes.get(c[0]["player_id"], [])
        if len(ms) >= 2:
            cons.append(_prop_bet("Consensus", date, *c[:3], source, posted, " + ".join(ms)))
    so_players = {b["player_id"] for b in by_model["Shots Over"]}
    for b in by_model["Goal Value"]:
        if b["player_id"] in so_players:
            c = next(c for c in gv if c[0]["player_id"] == b["player_id"])
            cons.append(_prop_bet("Consensus", date, *c[:3], source, posted, "Goal Value + Shots Over"))
    by_model["Consensus"] = cons

    # ---------------- game lines
    gbest = {}
    for o in offers:
        if o["market"] in ("ml", "pl", "total"):
            gbest.setdefault((o["home"], o["away"]), []).append(o)
    ml, pl, tot = [], [], []
    for g in games:
        for o in gbest.get((g["home"], g["away"]), []):
            if o["market"] == "ml" and o["side"] in (g["home"], g["away"]):
                p = g["home_win"] if o["side"] == g["home"] else g["away_win"]
                ml.append((g, o, p, ev(p, o["price"])))
            elif o["market"] == "pl" and o["side"] in (g["home"], g["away"]) and o["line"] in (-1.5, 1.5):
                key = ("home" if o["side"] == g["home"] else "away") + ("_m15" if o["line"] < 0 else "_p15")
                p = g[key]
                pl.append((g, o, p, ev(p, o["price"])))
            elif o["market"] == "total" and o["line"] is not None and o["side"] in ("over", "under"):
                po = M.p_total_over(g["lh"], g["la"], o["line"])
                p = po if o["side"] == "over" else 1 - po
                tot.append((g, o, p, ev(p, o["price"])))

    def gb(model, items, thr, n):
        items = [i for i in items if i[3] >= thr]
        seen, out = set(), []
        for g, o, p, e in sorted(items, key=lambda x: -x[3]):
            if g["game_id"] in seen:
                continue
            seen.add(g["game_id"])
            out.append(_game_bet(model, date, g, o["market"], o["side"], o["line"], o["price"], o["book"], p, source, posted))
            if len(out) >= n:
                break
        return out
    by_model["Moneyline Model"] = gb("Moneyline Model", ml, 0.04, 3)
    by_model["Puck Line Model"] = gb("Puck Line Model", pl, 0.05, 3)
    by_model["Totals Model"] = gb("Totals Model", tot, 0.04, 3)

    # ---------------- best card: top 5 by edge across every straight bet above
    pool, seen = [], set()
    for m, bs in by_model.items():
        for b in bs:
            k = (b["game_id"], b["market"], b["player_id"], b["side"], b["line"])
            if k in seen or b["ev"] < 0.06:
                continue
            seen.add(k)
            pool.append(b)
    card = []
    for b in sorted(pool, key=lambda b: -b["ev"])[:5]:
        c = dict(b, model="Best Card", note=f"from {b['model']}")
        c["bet_key"] = _key(date, "Best Card", b["game_id"], b["market"], b["player_id"], b["side"], b["line"])
        card.append(c)
    by_model["Best Card"] = card

    bets = [b for bs in by_model.values() for b in bs]
    return bets + make_parlays(date, prop_cands, source, posted)


def _one_per_player(items):
    seen, out = set(), []
    for c in items:
        if c[0]["player_id"] in seen:
            continue
        seen.add(c[0]["player_id"])
        out.append(c)
    return out


def make_parlays(date, prop_cands, source, posted):
    out = []
    pts = [c for c in prop_cands if c[1]["market"] == "points" and c[1]["side"] == "over" and c[1]["line"] == 0.5
           and c[2] >= 0.5]

    def pick(items, n, key):
        chosen, games = [], set()
        for c in sorted(items, key=key):
            if c[0]["game_id"] in games:
                continue
            chosen.append(c)
            games.add(c[0]["game_id"])
            if len(chosen) == n:
                return chosen
        return None
    defs = [("Points 2-leg", pick(pts, 2, lambda c: -c[2])), ("Points 3-leg", pick(pts, 3, lambda c: -c[2])),
            ("Value 2-leg", pick([c for c in prop_cands if c[3] >= 0.05 and c[2] >= 0.3], 2, lambda c: -c[3]))]
    for name, legs in defs:
        if not legs:
            continue
        dec = float(np.prod([M.american_to_decimal(c[1]["price"]) for c in legs]))
        p = float(np.prod([c[2] for c in legs]))
        price = int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))
        L = [{"player": c[0]["name"], "player_id": int(c[0]["player_id"]), "team": c[0]["team"],
              "game_id": int(c[0]["game_id"]), "market": c[1]["market"], "side": c[1]["side"], "line": c[1]["line"],
              "price": int(c[1]["price"]), "book": c[1]["book"], "p": round(float(c[2]), 4), "result": "pending"}
             for c in legs]
        out.append({"bet_key": _key(date, name, *[l["player_id"] for l in L]), "d": date, "game_id": L[0]["game_id"],
                    "start_utc": min(c[0]["start_utc"] for c in legs), "model": name, "kind": "parlay",
                    "market": "parlay", "bet": f"{len(L)}-leg parlay", "selection": " + ".join(l["player"] for l in L),
                    "player_id": None, "team": None, "opp": None, "side": None, "line": None, "price": price,
                    "book": "best", "p_model": round(p, 4), "ev": round(p * dec - 1, 4), "source": source,
                    "posted_utc": posted, "result": "pending", "units": None, "stat": None, "legs": L})
    return out


# ------------------------------------------------------------------ grading
def _leg_result(market, side, line, stat):
    if stat is None:
        return "void"
    if market == "goal":
        return "win" if stat >= 1 else "loss"
    if stat == line:
        return "push"
    over = stat > line
    return "win" if over == (side == "over") else "loss"


def grade(b, players, finals):
    """players: {player_id: {goals, points, sog}} for FINAL games; finals: {game_id: (home, away, hs, as)}.
    Returns the updated bet, or None if its game isn't final yet."""
    if b["kind"] == "parlay":
        legs, dec, done = [], 1.0, True
        for l in b["legs"]:
            if l["game_id"] not in finals:
                done = False
                legs.append(l)
                continue
            st = players.get(l["player_id"])
            stat = None if st is None else st[{"goal": "goals", "points": "points", "sog": "sog"}[l["market"]]]
            res = _leg_result(l["market"], l["side"], l["line"], stat)
            legs.append(dict(l, result=res, stat=stat))
        if not done:
            return None
        res = [l["result"] for l in legs]
        live = [l for l in legs if l["result"] in ("win", "loss")]
        if "loss" in res:
            out, units = "loss", -1.0
        elif not live:
            out, units = "void", 0.0
        else:
            dec = float(np.prod([M.american_to_decimal(l["price"]) for l in live]))
            out, units = "win", round(dec - 1, 3)
        return dict(b, legs=legs, result=out, units=units)
    if b["game_id"] not in finals:
        return None
    home, away, hs, as_ = finals[b["game_id"]]
    if b["kind"] == "prop":
        st = players.get(b["player_id"])
        stat = None if st is None else st[{"goal": "goals", "points": "points", "sog": "sog"}[b["market"]]]
        res = _leg_result(b["market"], b["side"], b["line"], stat)
    else:
        mine, theirs = (hs, as_) if b["side"] == home else (as_, hs)
        if b["market"] == "ml":
            stat = mine - theirs
            res = "win" if mine > theirs else "loss"
        elif b["market"] == "pl":
            stat = mine - theirs
            m = mine - theirs + b["line"]
            res = "win" if m > 0 else ("push" if m == 0 else "loss")
        else:
            stat = hs + as_
            res = _leg_result("total", b["side"], b["line"], stat)
    units = {"win": round(M.american_to_decimal(b["price"]) - 1, 3), "loss": -1.0}.get(res, 0.0)
    return dict(b, result=res, units=units, stat=stat)


# ------------------------------------------------------------------ records
def records(bets, today):
    cols = ["d", "model", "result", "units", "source"]
    df = pd.DataFrame(bets) if bets else pd.DataFrame(columns=cols)
    df["d"] = pd.to_datetime(df.d)
    df["units"] = pd.to_numeric(df.units, errors="coerce")
    g = df[df.result.isin(["win", "loss", "push", "void"])]
    cut = pd.Timestamp(today) - pd.Timedelta(days=14)
    out = []
    for name in list(MODELS) + list(PARLAYS):
        x = g[g.model == name]
        xs = x[x.result.isin(["win", "loss"])]
        live = xs[xs.source == "live"]
        rec = {"model": name, "desc": MODELS.get(name, {}).get("desc") or PARLAYS.get(name),
               "tag": MODELS.get(name, {}).get("tag", "Parlay"), "parlay": name in PARLAYS,
               "w": int((xs.result == "win").sum()), "l": int((xs.result == "loss").sum()),
               "p": int((x.result == "push").sum()), "units": round(float(xs.units.sum()), 2), "bets": int(len(xs)),
               "roi": round(float(xs.units.sum() / len(xs)), 4) if len(xs) else None,
               "l14": round(float(xs[xs.d >= cut].units.sum()), 2) if len(xs[xs.d >= cut]) else None,
               "live_w": int((live.result == "win").sum()), "live_l": int((live.result == "loss").sum()),
               "live_units": round(float(live.units.sum()), 2)}
        out.append(rec)
    return {"models": [r for r in out if not r["parlay"]], "parlays": [r for r in out if r["parlay"]]}
