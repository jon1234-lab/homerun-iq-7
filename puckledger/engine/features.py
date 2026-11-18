"""Pre-game features, computed walk-forward: every number for a game uses only games played before it.

Tonight's games are handled the same way: append them as rows with no outcome and read their features.
"""
import numpy as np
import pandas as pd

# position priors (all situations), per 60 minutes / per shot; from 2021-26 league totals
PRIOR = {"F": {"shot60": 7.6, "pts60": 2.05, "shpct": 0.108, "toi": 15.0 * 60, "pptoi": 1.4 * 60},
         "D": {"shot60": 4.6, "pts60": 1.15, "shpct": 0.050, "toi": 20.0 * 60, "pptoi": 0.8 * 60}}
HL = {"rate": 40, "shpct": 160, "toi": 4, "pptoi": 6, "team": 25, "goalie": 50}
K = {"shot_toi": 250 * 60, "pts_toi": 400 * 60, "shpct_shots": 150, "team_games": 12, "goalie_sa": 1000}


def _ew_pre(groups: np.ndarray, x: np.ndarray, hl: float) -> np.ndarray:
    """Exponentially decayed sum of x over each group's PREVIOUS rows (rows must be sorted by group then time)."""
    d = 0.5 ** (1.0 / hl)
    out = np.zeros(len(x), dtype=np.float64)
    acc = 0.0
    prev_g = None
    prev_x = 0.0
    for i in range(len(x)):
        g = groups[i]
        if g != prev_g:
            acc = 0.0
        else:
            acc = acc * d + prev_x
        out[i] = acc
        prev_g = g
        xi = x[i]
        prev_x = 0.0 if xi != xi else xi   # NaN (tonight's rows) adds nothing
    return out


def _ew_mean_pre(groups, x, hl):
    w = (~np.isnan(x)).astype(np.float64)
    s = _ew_pre(groups, np.nan_to_num(x), hl)
    n = _ew_pre(groups, w, hl)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan), n


def league_constants(tg: pd.DataFrame, gg: pd.DataFrame) -> dict:
    t = tg[tg.date >= tg.date.max() - pd.Timedelta(days=3 * 365)]
    g = gg[gg.date >= gg.date.max() - pd.Timedelta(days=3 * 365)]
    return {"sa": float(t.sa.mean()), "ga": float(t.ga_raw.mean()), "sf": float(t.sf.mean()),
            "sv": float(1 - g.ga.sum() / g.sa.sum())}


def team_features(tg: pd.DataFrame, gg: pd.DataFrame, lg: dict) -> pd.DataFrame:
    """Per team-game: the team's pre-game offense/defense rates (shrunk to league) and its starter's quality."""
    t = tg.sort_values(["team", "date", "gameId"]).reset_index(drop=True)
    grp = t.team.values
    out = t[["gameId", "team", "opp", "home", "date"]].copy()
    for col, prior in (("gf_raw", lg["ga"]), ("ga_raw", lg["ga"]), ("sf", lg["sf"]), ("sa", lg["sa"])):
        x = t[col].astype(float).values
        s = _ew_pre(grp, np.nan_to_num(x), HL["team"])
        n = _ew_pre(grp, (~np.isnan(x)).astype(float), HL["team"])
        out[col + "_rate"] = (s + prior * K["team_games"]) / (n + K["team_games"])
    out["gf_ratio"] = out.gf_raw_rate / lg["ga"]
    out["ga_ratio"] = out.ga_raw_rate / lg["ga"]
    out["sf_ratio"] = out.sf_rate / lg["sf"]
    out["sa_ratio"] = out.sa_rate / lg["sa"]
    # starting goalie per team-game and his pre-game save quality
    g = gg.sort_values(["playerId", "date", "gameId"]).reset_index(drop=True)
    gid = g.playerId.values
    sa = _ew_pre(gid, np.nan_to_num(g.sa.astype(float).values), HL["goalie"])
    ga = _ew_pre(gid, np.nan_to_num(g.ga.astype(float).values), HL["goalie"])
    g["g_sv"] = 1 - (ga + (1 - lg["sv"]) * K["goalie_sa"]) / (sa + K["goalie_sa"])
    g["g_factor"] = (1 - g.g_sv) / (1 - lg["sv"])
    st = g[g.started > 0].drop_duplicates(["gameId", "team"])[["gameId", "team", "playerId", "name", "g_sv", "g_factor"]]
    st = st.rename(columns={"playerId": "goalie_id", "name": "goalie"})
    out = out.merge(st, on=["gameId", "team"], how="left")
    out["g_factor"] = out.g_factor.fillna(1.05)   # unknown starter: assume a little worse than average
    return out


def skater_features(sg: pd.DataFrame, tf: pd.DataFrame) -> pd.DataFrame:
    s = sg.sort_values(["playerId", "date", "gameId"]).reset_index(drop=True)
    pid = s.playerId.values
    pri = pd.DataFrame(PRIOR).T
    p = pri.loc[s.pos.values]
    toi = s.toi.values.astype(float)
    shots, goals, pts = (s[c].values.astype(float) for c in ("shots", "goals", "points"))
    S_toi = _ew_pre(pid, np.nan_to_num(toi), HL["rate"])
    S_sh = _ew_pre(pid, np.nan_to_num(shots), HL["rate"])
    S_pt = _ew_pre(pid, np.nan_to_num(pts), HL["rate"])
    s["shot60"] = (S_sh + p.shot60.values / 3600 * K["shot_toi"]) / (S_toi + K["shot_toi"]) * 3600
    s["pts60"] = (S_pt + p.pts60.values / 3600 * K["pts_toi"]) / (S_toi + K["pts_toi"]) * 3600
    L_sh = _ew_pre(pid, np.nan_to_num(shots), HL["shpct"])
    L_g = _ew_pre(pid, np.nan_to_num(goals), HL["shpct"])
    s["shpct"] = (L_g + p.shpct.values * K["shpct_shots"]) / (L_sh + K["shpct_shots"])
    tp, n = _ew_mean_pre(pid, toi, HL["toi"])
    s["toi_proj"] = np.where(np.isnan(tp), p.toi.values, tp)
    pp, _ = _ew_mean_pre(pid, s.pptoi.values.astype(float), HL["pptoi"])
    s["pptoi_proj"] = np.where(np.isnan(pp), p.pptoi.values * 0.3, pp)
    s["n_prior"] = _ew_pre(pid, np.ones(len(s)), 10_000)   # ~ count of previous games
    own = tf[["gameId", "team", "gf_ratio", "sf_ratio"]].rename(columns={"gf_ratio": "team_gf", "sf_ratio": "team_sf"})
    opp = tf[["gameId", "team", "ga_ratio", "sa_ratio", "g_factor", "goalie"]].rename(
        columns={"team": "opp", "ga_ratio": "opp_ga", "sa_ratio": "opp_sa", "g_factor": "opp_gf", "goalie": "opp_goalie"})
    s = s.merge(own, on=["gameId", "team"], how="left").merge(opp, on=["gameId", "opp"], how="left")
    for c in ("team_gf", "team_sf", "opp_ga", "opp_sa", "opp_gf"):
        s[c] = s[c].fillna(1.0)
    # model inputs (logs)
    s["x_shots"] = np.log(s.shot60 * s.toi_proj / 3600)
    s["x_pts"] = np.log(s.pts60 * s.toi_proj / 3600)
    s["x_shpct"] = np.log(s.shpct)
    s["x_pp"] = np.log1p(s.pptoi_proj / 60)
    for c in ("team_gf", "team_sf", "opp_ga", "opp_sa", "opp_gf"):
        s["x_" + c] = np.log(s[c])
    return s


SHOT_X = ["x_shots", "x_opp_sa", "x_pp", "home"]
GOAL_X = ["x_shots", "x_shpct", "x_opp_sa", "x_opp_gf", "x_opp_ga", "x_pp", "x_team_gf", "home"]
PTS_X = ["x_pts", "x_team_gf", "x_opp_ga", "x_opp_gf", "x_pp", "home"]
TEAM_X = ["x_gf", "x_sf", "x_opp_ga", "x_opp_sa", "x_opp_gfac", "home"]


def team_game_features(tf: pd.DataFrame) -> pd.DataFrame:
    o = tf[["gameId", "team", "ga_ratio", "sa_ratio", "g_factor", "goalie"]].rename(
        columns={"team": "opp", "ga_ratio": "opp_ga", "sa_ratio": "opp_sa", "g_factor": "opp_gfac", "goalie": "opp_goalie"})
    x = tf.merge(o, on=["gameId", "opp"], how="left")
    x["x_gf"] = np.log(x.gf_ratio)
    x["x_sf"] = np.log(x.sf_ratio)
    x["x_opp_ga"] = np.log(x.opp_ga.fillna(1))
    x["x_opp_sa"] = np.log(x.opp_sa.fillna(1))
    x["x_opp_gfac"] = np.log(x.opp_gfac.fillna(1.05))
    return x
