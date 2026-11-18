"""Free research tools: player vs every team, streaks, form / prop hit rates, team betting trends."""
import numpy as np
import pandas as pd


def _cur(sg):
    return sg[sg.season == sg.season.max()]


def team_names(tg):
    t = tg.sort_values("date").drop_duplicates("team", keep="last")
    return dict(zip(t.team, t.teamFullName))


def player_index(sg):
    s = sg[sg.season >= sg.season.max() - 10001].sort_values("date")
    last = s.drop_duplicates("playerId", keep="last")
    cur = _cur(sg).groupby("playerId").agg(gp=("gameId", "size"), pts=("points", "sum"))
    last = last.merge(cur, left_on="playerId", right_index=True, how="left").fillna({"gp": 0, "pts": 0})
    last = last.sort_values(["pts", "gp"], ascending=False)
    return [{"id": int(r.playerId), "name": r.name, "team": r.team, "pos": r.pos, "gp": int(r.gp), "pts": int(r.pts)}
            for r in last.itertuples()]


def vs_table(pid, sg, vs_hist):
    """Career (since 2015-16) and this-season splits against every opponent."""
    cur = _cur(sg)
    c = cur[cur.playerId == pid]
    rows = vs_hist[vs_hist.playerId == pid].copy()
    if len(c):
        add = c.groupby("opp").agg(gp=("gameId", "size"), g=("goals", "sum"), a=("assists", "sum"), pts=("points", "sum"),
                                   sog=("shots", "sum"), ppp=("ppp", "sum"), gw_pt=("points", lambda x: (x > 0).sum()),
                                   gw_g=("goals", lambda x: (x > 0).sum()), last=("date", "max")).reset_index()
        add["playerId"] = pid
        rows = pd.concat([rows, add]).groupby(["playerId", "opp"]).agg(
            gp=("gp", "sum"), g=("g", "sum"), a=("a", "sum"), pts=("pts", "sum"), sog=("sog", "sum"), ppp=("ppp", "sum"),
            gw_pt=("gw_pt", "sum"), gw_g=("gw_g", "sum"), last=("last", "max")).reset_index()
        season = c.groupby("opp").agg(s_gp=("gameId", "size"), s_pts=("points", "sum"), s_g=("goals", "sum")).reset_index()
        rows = rows.merge(season, on="opp", how="left")
    out = []
    for r in rows.itertuples():
        gp = int(r.gp)
        out.append({"opp": r.opp, "gp": gp, "g": int(r.g), "a": int(r.a), "pts": int(r.pts), "sog": int(r.sog),
                    "ppp": int(r.ppp), "ppg": round(r.pts / gp, 2), "gpg": round(r.g / gp, 2), "sogpg": round(r.sog / gp, 2),
                    "pt_pct": round(r.gw_pt / gp, 3), "g_pct": round(r.gw_g / gp, 3),
                    "last": pd.Timestamp(r.last).date().isoformat(),
                    "s_gp": int(getattr(r, "s_gp", 0) or 0) if not pd.isna(getattr(r, "s_gp", np.nan)) else 0,
                    "s_pts": int(getattr(r, "s_pts", 0) or 0) if not pd.isna(getattr(r, "s_pts", np.nan)) else 0})
    out.sort(key=lambda x: (-x["ppg"], -x["gp"]))
    who = sg[sg.playerId == pid].sort_values("date").tail(1)
    info = {} if who.empty else {"id": int(pid), "name": who.name.iloc[0], "team": who.team.iloc[0], "pos": who.pos.iloc[0]}
    return {"player": info, "rows": out}


def _streak(vals):
    n = 0
    for v in vals[::-1]:
        if v:
            n += 1
        else:
            break
    return n


def form(sg):
    """Per player this season: streaks and recent hit rates (from games already played)."""
    c = _cur(sg).sort_values("date")
    c = c[c.points.notna()]
    out = {}
    for pid, g in c.groupby("playerId", sort=False):
        pts, gl, sog = g.points.values, g.goals.values, g.shots.values
        last = g.iloc[-1]
        l10 = slice(-10, None)
        l5 = slice(-5, None)
        out[int(pid)] = {
            "name": last["name"], "team": last.team, "pos": last.pos, "gp": int(len(g)),
            "pt_streak": _streak(pts > 0), "g_streak": _streak(gl > 0), "sog3_streak": _streak(sog >= 3),
            "multi_streak": _streak(pts >= 2), "dry": _streak(pts == 0),
            "l5_pt": float((pts[l5] > 0).mean()), "l10_pt": float((pts[l10] > 0).mean()), "s_pt": float((pts > 0).mean()),
            "l5_g": float((gl[l5] > 0).mean()), "l10_g": float((gl[l10] > 0).mean()), "s_g": float((gl > 0).mean()),
            "l10_sog2": float((sog[l10] >= 2).mean()), "l10_sog3": float((sog[l10] >= 3).mean()),
            "s_sog3": float((sog >= 3).mean()), "ppg": float(pts.mean()), "sogpg": float(sog.mean()),
            "streak_pts": int(pts[len(pts) - _streak(pts > 0):].sum()) if _streak(pts > 0) else 0,
            "last_date": pd.Timestamp(last.date).date().isoformat(),
            "last5": [int(x) for x in pts[l5]],
        }
    return out


def streak_lists(fm, last_game_date, min_gp=5):
    """Only players whose most recent game is recent (still active)."""
    cutoff = (pd.Timestamp(last_game_date) - pd.Timedelta(days=6)).date().isoformat()
    act = {k: v for k, v in fm.items() if v["last_date"] >= cutoff and v["gp"] >= min_gp}

    def lst(key, minv, extra=None):
        rows = [dict(id=k, **{x: v[x] for x in ("name", "team", "pos", "gp", "ppg", "l10_pt", "last_date")},
                     streak=v[key], streak_pts=v["streak_pts"]) for k, v in act.items() if v[key] >= minv]
        return sorted(rows, key=lambda r: (-r["streak"], -r["ppg"]))[:60]
    cold = [dict(id=k, name=v["name"], team=v["team"], pos=v["pos"], gp=v["gp"], ppg=round(v["ppg"], 2), streak=v["dry"])
            for k, v in act.items() if v["dry"] >= 4 and v["ppg"] >= 0.6]
    return {"points": lst("pt_streak", 3), "goals": lst("g_streak", 2), "sog3": lst("sog3_streak", 3),
            "multi": lst("multi_streak", 2), "cold": sorted(cold, key=lambda r: -r["streak"])[:40]}


def team_trends(tg, posted_totals=None):
    """posted_totals: {(game_id): total line} from our odds snapshots, when we have them."""
    posted_totals = posted_totals or {}
    c = tg[tg.season == tg.season.max()].sort_values("date")
    c = c[c.gf.notna()]
    names = team_names(tg)
    out = []
    for team, g in c.groupby("team"):
        n = len(g)
        tot = (g.gf + g.ga).values
        margin = (g.gf - g.ga).values
        home, away = g[g.home == 1], g[g.home == 0]
        lines = [posted_totals.get(int(gid)) for gid in g.gameId]
        ou = [(t > l) - (t < l) for t, l in zip(tot, lines) if l is not None]
        l10 = g.tail(10)

        def rec(x):
            w = int(x.win.sum())
            otl = int(((x.win == 0) & (x.went_ot == 1)).sum())
            return f"{w}-{len(x) - w - otl}-{otl}"
        out.append({"team": team, "name": names.get(team, team), "gp": n, "record": rec(g), "home": rec(home),
                    "away": rec(away), "l10": rec(l10), "win_pct": round(g.win.mean(), 3),
                    "pl_minus": round(float((margin >= 2).mean()), 3), "pl_plus": round(float((margin >= -1).mean()), 3),
                    "gf": round(float(g.gf.mean()), 2), "ga": round(float(g.ga.mean()), 2), "total": round(float(tot.mean()), 2),
                    "o55": round(float((tot > 5.5).mean()), 3), "o65": round(float((tot > 6.5).mean()), 3),
                    "ou_n": len(ou), "over": sum(1 for x in ou if x > 0), "under": sum(1 for x in ou if x < 0),
                    "ot_pct": round(float(g.went_ot.mean()), 3), "last5": [int(x) for x in g.win.values[-5:]]})
    return sorted(out, key=lambda r: -r["win_pct"])
