"""Projections for any date: every player's chance of a goal / point / N shots and every game's
win, puck-line and total probabilities. Uses only games before that date."""
import numpy as np
import pandas as pd

from . import features as F
from . import model as M

DRIFT_SHRINK = {"shots": 1.0, "goals": 0.5}


def append_tonight(sg, tg, gg, date, games, skaters, starters):
    """Add tonight's not-yet-played rows. games: [{game_id, home, away}];
    skaters: DataFrame[playerId, name, pos, team]; starters: {team: (goalie_id, name)}."""
    d = pd.Timestamp(date)
    season = int(sg.season.max())
    side = {}
    trows = []
    for g in games:
        for team, opp, home in ((g["home"], g["away"], 1), (g["away"], g["home"], 0)):
            side[team] = (g["game_id"], opp, home)
            trows.append({"season": season, "gameId": g["game_id"], "date": d, "team": team, "opp": opp, "home": home})
    srows = skaters[skaters.team.isin(side)].copy()
    srows["gameId"] = srows.team.map(lambda t: side[t][0])
    srows["opp"] = srows.team.map(lambda t: side[t][1])
    srows["home"] = srows.team.map(lambda t: side[t][2]).astype(np.int8)
    srows["date"], srows["season"] = d, season
    grows = [{"season": season, "gameId": side[t][0], "date": d, "playerId": gid, "name": nm, "team": t,
              "opp": side[t][1], "started": 1} for t, (gid, nm) in starters.items() if t in side and gid]
    return (pd.concat([sg, srows], ignore_index=True), pd.concat([tg, pd.DataFrame(trows)], ignore_index=True),
            pd.concat([gg, pd.DataFrame(grows)], ignore_index=True))


def likely_starters(gg, teams, before):
    """Fallback starter: the goalie with the most starts in the team's last 10 games."""
    g = gg[(gg.date < pd.Timestamp(before)) & (gg.started > 0)].sort_values("date")
    out = {}
    for t in teams:
        r = g[g.team == t].tail(10)
        if len(r):
            top = r.groupby(["playerId", "name"]).size().sort_values().index[-1]
            out[t] = (int(top[0]), top[1])
    return out


def compute(sg, tg, gg, drift=None):
    """drift: optional {"shots": f, "goals": f} to use for rows with no outcome yet (tonight)."""
    lg = F.league_constants(tg[tg.gf_raw.notna()], gg[gg.sa.notna()])
    tf = F.team_features(tg, gg, lg)
    sf = F.skater_features(sg, tf)
    sf["mu_shots"] = M.lam("shots", sf)
    sf["mu_goals"] = M.lam("goals", sf)
    sf["mu_points"] = M.lam("points", sf)
    # walk-forward drift correction: actual / predicted over the previous 30 game-days
    played = sf[sf.toi > 0]
    daily = played.groupby("date").agg(a_s=("shots", "sum"), p_s=("mu_shots", "sum"),
                                       a_g=("goals", "sum"), p_g=("mu_goals", "sum"))
    all_days = pd.Index(sorted(sf.date.unique()))
    daily = daily.reindex(all_days)
    for m, a, p in (("shots", "a_s", "p_s"), ("goals", "a_g", "p_g")):
        ratio = (daily[a].shift(1).rolling(30, min_periods=5).sum() / daily[p].shift(1).rolling(30, min_periods=5).sum())
        f = 1 + (ratio.clip(0.85, 1.15).fillna(1) - 1) * DRIFT_SHRINK[m]
        sf["f_" + m] = sf.date.map(f).astype(float)
        if drift and m in drift:
            sf.loc[sf.toi.isna(), "f_" + m] = drift[m]
    sf["mu_shots"] *= sf.f_shots
    sf["mu_goals"] *= sf.f_goals
    sf["p_goal"] = 1 - np.exp(-sf.mu_goals)
    sf["p_point"] = 1 - np.exp(-sf.mu_points)
    sf["p_2pts"] = M.p_at_least(sf.mu_points.values, 2)
    for k in (2, 3, 4):
        sf[f"p_sog{k}"] = M.p_over_shots(sf.mu_shots.values, k - 0.5)
    gx = F.team_game_features(tf)
    gx["lam"] = M.lam("team", gx)
    return sf, gx


def next_drift(sf):
    """Drift factors for the next unplayed day: last 30 game-days including the most recent."""
    played = sf[sf.toi > 0]
    daily = played.groupby("date").agg(a_s=("shots", "sum"), p_s=("mu_shots", "sum"), a_g=("goals", "sum"),
                                       p_g=("mu_goals", "sum"), fs=("f_shots", "first"), fg=("f_goals", "first")).tail(30)
    if len(daily) < 5:
        return {"shots": 1.0, "goals": 1.0}
    # mu_* already include each day's factor; undo it to compare raw predictions with actuals
    rs = daily.a_s.sum() / (daily.p_s / daily.fs).sum()
    rg = daily.a_g.sum() / (daily.p_g / daily.fg).sum()
    return {"shots": float(1 + (min(max(rs, 0.85), 1.15) - 1) * DRIFT_SHRINK["shots"]),
            "goals": float(1 + (min(max(rg, 0.85), 1.15) - 1) * DRIFT_SHRINK["goals"])}


def game_table(gx, game_ids):
    h = gx[(gx.home == 1) & gx.gameId.isin(game_ids)]
    a = gx[(gx.home == 0) & gx.gameId.isin(game_ids)][["gameId", "team", "lam", "goalie"]]
    h = h.merge(a, on="gameId", suffixes=("", "_a"))
    rows = []
    for r in h.itertuples():
        p = M.game_probs(r.lam, r.lam_a)
        rows.append({"game_id": int(r.gameId), "home": r.team, "away": r.team_a, "home_goalie": r.goalie,
                     "away_goalie": r.goalie_a, **p})
    return rows
