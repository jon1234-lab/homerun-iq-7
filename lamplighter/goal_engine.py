"""GoalIQ anytime-goalscorer model.

For each skater:  lambda = expected goals tonight,  P(goal) = 1 - exp(-lambda)  (Poisson).

lambda = r_eff * eff_minutes_tonight * opp_goalie_mult * opp_defense_mult * home_mult * CAL

  r_eff   : goals per "effective minute" (PP minutes count PP_W x, SH minutes SH_W x an EV minute).
            Shot-based: regressed shot rate * regressed shooting % (toward position means).
  minutes : projected EV/PP/SH TOI from history, nudged toward tonight's line slot and PP unit.
  goalie  : opposing starter's regressed save %:  (1 - sv_reg) / (1 - sv_league)
  defense : opponent shots-against/game vs league, regressed 50%.
  CAL     : global scalar fitted in the leak-free backtest (calibration.json).

Backtest 2025-26 (10,115 player-games, pre-game data only):
  log loss 0.3983 vs naive goals/game 0.4187 vs base rate 0.4285; calibrated within ~2pts per decile.
"""
import math

PP_W, SH_W = 2.6, 0.35
K_SHOTS_SH = {"F": 300, "D": 400}      # shots of prior for shooting %
K_TOI_SHOTS = 600 * 60                 # seconds of prior for shot rate (~600 min)
K_GOALIE_SA = 1500                     # shots of prior for goalie save %
HOME_MULT = 1.03
PRIOR_SEASON_WEIGHT = 0.5
CAL = 1.0

SLOT_EV = {"f1": 15.0, "f2": 13.3, "f3": 11.5, "f4": 9.3, "d1": 19.0, "d2": 17.0, "d3": 14.5}
SLOT_PP = {"pp1": 2.9, "pp2": 1.1, None: 0.15}
SLOT_BLEND = 0.4
STAT_KEYS = ("gp", "goals", "pp_goals", "shots", "toi", "pp_toi", "sh_toi", "ev_toi")


def pgroup(pos):
    return "D" if pos == "D" else "F"


def empty(pos):
    return {"pos": pos, **{k: 0 for k in STAT_KEYS}}


def combine(cur, prior, w_prior=PRIOR_SEASON_WEIGHT):
    out = {}
    for pid in set(cur) | set(prior):
        c, p = cur.get(pid), prior.get(pid)
        base = dict(c or p)
        for k in STAT_KEYS:
            base[k] = (c or {}).get(k, 0) + w_prior * (p or {}).get(k, 0)
        out[pid] = base
    return out


def league_constants(sk, goalies):
    lc = {}
    for g in ("F", "D"):
        rows = [v for v in sk.values() if pgroup(v["pos"]) == g and v["toi"] > 0]
        shots = sum(v["shots"] for v in rows)
        goals = sum(v["goals"] for v in rows)
        toi = sum(v["toi"] for v in rows)
        eff = sum(v["ev_toi"] + PP_W * v["pp_toi"] + SH_W * v["sh_toi"] for v in rows)
        lc[g] = {"sh_pct": goals / max(shots, 1), "shots_per_sec": shots / max(toi, 1),
                 "shots_per_eff": shots / max(eff, 1)}
    sa = sum(v["sa"] for v in goalies.values())
    ga = sum(v["ga"] for v in goalies.values())
    lc["sv"] = 1 - ga / max(sa, 1)
    return lc


def player_rate(v, lc):
    g = pgroup(v["pos"])
    L = lc[g]
    eff = v["ev_toi"] + PP_W * v["pp_toi"] + SH_W * v["sh_toi"]
    shot_rate = (v["shots"] + L["shots_per_eff"] * K_TOI_SHOTS) / (eff + K_TOI_SHOTS)
    k = K_SHOTS_SH[g]
    sh = (v["goals"] + L["sh_pct"] * k) / (v["shots"] + k)
    return shot_rate * sh, shot_rate, sh


def projected_minutes(v, line=None, pp=None):
    has = v["gp"] >= 1
    gp = max(v["gp"], 1e-9)
    ev = v["ev_toi"] / gp / 60 if has else None
    ppm = v["pp_toi"] / gp / 60 if has else None
    shm = v["sh_toi"] / gp / 60 if has else 0.5
    if line in SLOT_EV:
        ev = SLOT_EV[line] if ev is None else (1 - SLOT_BLEND) * ev + SLOT_BLEND * SLOT_EV[line]
    if ev is None:
        ev = 11.0 if pgroup(v["pos"]) == "F" else 15.5
    if line is not None or pp is not None:
        tgt = SLOT_PP.get(pp, 0.15)
        ppm = tgt if ppm is None else (1 - SLOT_BLEND) * ppm + SLOT_BLEND * tgt
    if ppm is None:
        ppm = 0.5
    return ev, ppm, shm


def goalie_mult(gstats, lc):
    if not gstats:
        return 1.0, lc["sv"]
    sv_reg = (gstats["sa"] - gstats["ga"] + lc["sv"] * K_GOALIE_SA) / (gstats["sa"] + K_GOALIE_SA)
    return (1 - sv_reg) / (1 - lc["sv"]), sv_reg


def defense_mult(team_sa_pg, league_sa_pg):
    if not team_sa_pg or not league_sa_pg:
        return 1.0
    return 1 + 0.5 * (team_sa_pg / league_sa_pg - 1)


def predict(v, lc, *, line=None, pp=None, opp_goalie=None, opp_sa_pg=None,
            league_sa_pg=None, home=False, cal=None):
    rate, shot_rate, sh = player_rate(v, lc)
    ev, ppm, shm = projected_minutes(v, line, pp)
    eff_sec = (ev + PP_W * ppm + SH_W * shm) * 60
    gm, sv_reg = goalie_mult(opp_goalie, lc)
    dm = defense_mult(opp_sa_pg, league_sa_pg)
    lam = rate * eff_sec * gm * dm * (HOME_MULT if home else 1 / HOME_MULT) * (cal or CAL)
    return {"lambda": lam, "p_goal": 1 - math.exp(-lam),
            "exp_shots": shot_rate * eff_sec, "sh_pct_reg": sh,
            "proj_ev_min": ev, "proj_pp_min": ppm, "goalie_mult": gm, "defense_mult": dm,
            "opp_sv_reg": sv_reg}


def fair_american(p):
    if p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)
