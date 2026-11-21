"""Plain-English reasons for each pick, written from the model's own inputs.

Every reason is tied to a number the model uses (shot volume, chance quality, ice time, power play,
finishing, opposing goalie, opposing defense, rest). Reasons are ranked by how unusual the number is
on tonight's slate, so the strongest case comes first. A "watch out" note is added when something
cuts against the pick.
"""
import bisect

MARKETS = ("goal", "point", "sog")


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _ord(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _mmss(m):
    m = _f(m)
    if m is None:
        return ""
    s = int(round(m * 60))
    return f"{s // 60}:{s % 60:02d}"


def norm(r):
    """A board snapshot row (pipeline format) in the shape the reasons need."""
    opp = str(r.get("opp") or "")
    g = (r.get("opp_goalie") or "").strip()
    return {
        "id": int(r.get("playerId") or r.get("player_id") or 0), "name": r.get("player") or r.get("name"),
        "team": r.get("team"), "opp": opp.replace("vs ", "").replace("@ ", "").strip(), "home": opp.startswith("vs"),
        "pos": r.get("pos"), "sog_proj": _f(r.get("sog_proj")), "toi": _f(r.get("toi")), "pp_toi": _f(r.get("pp_toi")),
        "xg60": _f(r.get("xg60")), "fin": _f(r.get("finishing")), "goalie": g.rstrip(" *").strip(), "goalie_proj": g.endswith("*"),
        "opp_def": _f(r.get("opp_def")), "opp_sa": _f(r.get("opp_sa")), "gk": _f(r.get("gk")),
        "b2b": r.get("b2b"), "opp_b2b": r.get("opp_b2b"), "sog": _f(r.get("sog")), "pts_pg": _f(r.get("pts_pg")),
        "p_goal": _f(r.get("p_goal")), "p_point": _f(r.get("p_point")), "p_sog3": _f(r.get("p_sog3")),
    }


class Slate:
    """Where a number ranks among tonight's skaters (all positions, or defensemen only)."""
    KEYS = ("sog_proj", "xg60", "toi", "pp_toi", "pts_pg", "sog")

    def __init__(self, rows):
        self.n = len(rows)
        self.vals = {k: sorted(v for v in (r.get(k) for r in rows) if v is not None) for k in self.KEYS}
        d = [r for r in rows if r.get("pos") == "D"]
        self.dvals = {k: sorted(v for v in (r.get(k) for r in d) if v is not None) for k in self.KEYS}

    def rank(self, k, v, d=False):
        """(rank from the top, percentile, group size)."""
        arr = (self.dvals if d else self.vals).get(k) or []
        if v is None or not arr:
            return None, 0.0, 0
        above = len(arr) - bisect.bisect_right(arr, v)
        return above + 1, bisect.bisect_left(arr, v) / max(1, len(arr) - 1), len(arr)

    def phrase(self, k, v, d=False, what="most"):
        rk, pct, n = self.rank(k, v, d)
        where = "among tonight's defensemen" if d else "on tonight's slate"
        if rk is None:
            return "", 0.0
        if rk == 1:
            return f"the {what} {where}", 1.0
        if rk <= 10:
            return f"{_ord(rk)} {what} {where}", pct
        if pct >= .9:
            return f"top 10% {where}", pct
        if pct >= .75:
            return f"top quarter {where}", pct
        return "", pct


def reasons(r, market, slate, pp_unit=None, line=None):
    """Up to three reasons (strongest first) and up to one 'watch out', each a short sentence."""
    if not r:
        return {"for": [], "against": []}
    d = r.get("pos") == "D"
    pros, cons = [], []   # (score, text)

    def add(lst, score, text):
        if text:
            lst.append((score, text))

    sog, xg, toi, pp = r.get("sog_proj"), r.get("xg60"), r.get("toi"), r.get("pp_toi")
    # shot volume
    if sog is not None:
        ph, pct = slate.phrase("sog_proj", sog, d)
        if ph:
            w = 1.15 if market == "sog" else 1.0
            add(pros, pct * w, f"Projected for {sog:.1f} shots on goal, {ph}.")
    # chance quality (goals and points)
    if xg is not None and market in ("goal", "point"):
        ph, pct = slate.phrase("xg60", xg, d, "best")
        if ph:
            add(pros, pct * (1.05 if market == "goal" else .85),
                f"Creates {xg:.2f} expected goals per 60 minutes, {ph}." if market == "goal"
                else f"Creates {xg:.2f} expected goals per 60 minutes, so the puck is around him in scoring areas.")
    # power play
    if pp is not None and pp >= 2.0:
        unit = "the top power-play unit" if (pp_unit or "").lower() == "pp1" or pp >= 2.8 else "the power play"
        ph, pct = slate.phrase("pp_toi", pp, d)
        add(pros, max(pct, .7) * (1.0 if market != "sog" else .85),
            f"Plays on {unit}: {_mmss(pp)} a night with the man advantage" + (f", {ph}." if ph else "."))
    # ice time
    if toi is not None:
        ph, pct = slate.phrase("toi", toi, d)
        if ph:
            add(pros, pct * (.9 if market == "sog" else .75), f"Logs {_mmss(toi)} of ice time a night, {ph}.")
        elif toi < 13.5:
            add(cons, .6, f"Limited minutes: about {_mmss(toi)} a night.")
    # finishing talent (goals)
    fin = r.get("fin")
    if fin is not None and market == "goal":
        if fin >= 1.08:
            add(pros, .55 + min(.4, (fin - 1) * 2.5), f"A proven finisher: scores {round((fin - 1) * 100)}% more goals than his chances alone would predict.")
        elif fin <= .9:
            add(cons, .55, f"Has scored {round((1 - fin) * 100)}% fewer goals than his chances would predict.")
    # point pace
    pts = r.get("pts_pg")
    if pts is not None and market == "point":
        ph, pct = slate.phrase("pts_pg", pts, d)
        if ph or pts >= .8:
            add(pros, max(pct, .75) * 1.25, f"Producing {pts:.2f} points a game (weighted toward recent games)" + (f", {ph}." if ph else "."))
    # recent shots
    rs = r.get("sog")
    if rs is not None and market == "sog" and sog:
        if rs >= sog * 1.08 and rs >= 2.5:
            add(pros, .7, f"Shooting even more lately: {rs:.1f} shots a game over his recent games.")
        elif rs <= sog * .8:
            add(cons, .5, f"Shot volume has dipped lately ({rs:.1f} a game in recent games).")
    # opposing goalie (goals and points)
    gk, g = r.get("gk"), r.get("goalie")
    status = " (projected starter)" if r.get("goalie_proj") else ""
    if gk is not None and g and market in ("goal", "point"):
        if gk >= 1.04:
            add(pros, .55 + min(.4, (gk - 1) * 4), f"Faces {g}{status}, who has allowed {round((gk - 1) * 100)}% more goals than expected.")
        elif gk <= .96:
            add(cons, .55 + min(.4, (1 - gk) * 4), f"Faces {g}{status}, who has allowed {round((1 - gk) * 100)}% fewer goals than expected.")
    # opposing defense
    od, opp = r.get("opp_def"), r.get("opp")
    if od is not None and market in ("goal", "point"):
        if od >= 1.05:
            add(pros, .5 + min(.4, (od - 1) * 3), f"{opp} gives up {round((od - 1) * 100)}% more expected goals than an average team.")
        elif od <= .93:
            add(cons, .5 + min(.4, (1 - od) * 3), f"{opp} is a stingy defense, allowing {round((1 - od) * 100)}% fewer expected goals than average.")
    osa = r.get("opp_sa")
    if osa is not None and market == "sog":
        if osa >= 1.04:
            add(pros, .5 + min(.4, (osa - 1) * 3), f"{opp} allows {round((osa - 1) * 100)}% more shots than an average team.")
        elif osa <= .94:
            add(cons, .5 + min(.4, (1 - osa) * 3), f"{opp} limits shots: {round((1 - osa) * 100)}% fewer allowed than average.")
    # rest
    if r.get("opp_b2b"):
        add(pros, .45, f"{opp} is on the second night of a back-to-back.")
    if r.get("b2b"):
        add(cons, .45, f"{r.get('team')} played last night (second half of a back-to-back).")
    if r.get("goalie_proj") and g and market in ("goal", "point") and not any(g in t for _, t in pros + cons):
        add(cons, .3, f"{opp} hasn't confirmed its starter; the model assumes {g}.")

    if not pros:   # nothing stands out: say what's holding the number down
        bits = [f"{sog:.1f} projected shots" if sog is not None else "", f"{_mmss(toi)} a night" if toi is not None else "",
                f"{_mmss(pp)} on the power play" if pp else "little power-play time"]
        _, pct, _n = slate.rank("sog_proj", sog, d)
        lead = "Middle-of-the-pack numbers" if pct >= .4 else "Below-average volume"
        pros.append((0, f"{lead}: {', '.join(b for b in bits if b)}."))
    pros.sort(key=lambda x: -x[0]); cons.sort(key=lambda x: -x[0])
    return {"for": [t for _, t in pros[:3]], "against": [t for _, t in cons[:1]]}
