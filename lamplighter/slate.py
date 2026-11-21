"""Build a night's goalscorer predictions. Used by the API, the cron job, and the CLI.

    python slate.py 2026-10-12
"""
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import goal_engine as E
import nhl_data as D

# regular-season start dates (add next season's each summer)
SEASONS = {
    "20242025": "2024-10-04",
    "20252026": "2025-10-07",
    "20262027": "2026-09-29",
}
# early in a season, last season counts as "current" so a handful of games doesn't dominate
EARLY_SEASON_DAYS = 30
HERE = os.path.dirname(os.path.abspath(__file__))


def _cal():
    try:
        return json.load(open(os.path.join(HERE, "calibration.json")))["cal"]
    except Exception:
        return 1.0


def _day_before(d):
    return (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()


def _season_windows(date):
    starts = sorted(SEASONS.values())
    cur_start = max(s for s in starts if s <= date)
    i = starts.index(cur_start)
    prev = _day_before(date)
    last = (starts[i - 1], _day_before(cur_start))
    two = (starts[i - 2], _day_before(starts[i - 1]))
    so_far = [(cur_start, prev)] if prev >= cur_start else []
    if (dt.date.fromisoformat(date) - dt.date.fromisoformat(cur_start)).days < EARLY_SEASON_DAYS:
        return so_far + [last], two
    return so_far, last


def _sum_windows(dicts):
    out = {}
    for d in dicts:
        for pid, v in d.items():
            if pid not in out:
                out[pid] = dict(v)
                continue
            for k, x in v.items():
                if isinstance(x, (int, float)) and k in out[pid]:
                    out[pid][k] += x
            out[pid]["team"] = out[pid].get("team") or v.get("team")
    return out


def _flat(xs):
    out = []
    for x in xs:
        out.extend(x if isinstance(x, list) else [x])
    return out


def _fuzzy(key, pool):
    """Match ignoring middle names, then by last name + first initial."""
    parts = key.split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    hits = _flat([p for k, p in pool.items() if k.split()[-1] == last and k.split()[0] == first])
    if hits:
        return hits
    hits = _flat([p for k, p in pool.items() if k.split()[-1] == last and k[0] == first[0]])
    return hits or None


def _pick(cands, line):
    """Choose among same-name players by position (e.g. VAN has two Elias Petterssons)."""
    if not cands:
        return None
    cands = cands if isinstance(cands, list) else [cands]
    if line:
        want_d = line.startswith("d")
        same = [p for p in cands if (p["pos"] == "D") == want_d]
        return same[0] if same else None
    return cands[0]


def build(date):
    games = D.schedule(date)
    if not games:
        return {"date": date, "games": [], "players": [],
                "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "model": {"calibration": _cal(), "version": "goaliq-v1"}}
    cur_ws, prior_w = _season_windows(date)
    teams = sorted({t for g in games for t in (g["home"], g["away"])})

    with ThreadPoolExecutor(12) as ex:
        f_sk = [ex.submit(D.skaters_window, *w) for w in cur_ws]
        f_g = [ex.submit(D.goalies_window, *w) for w in cur_ws]
        f_psk = ex.submit(D.skaters_window, *prior_w)
        f_pg = ex.submit(D.goalies_window, *prior_w)
        f_tsa = ex.submit(D.team_sa_window, *cur_ws[-1])
        f_sg = ex.submit(D.starting_goalies)
        f_rost = {t: ex.submit(D.roster, t) for t in teams}
        f_lines = {t: ex.submit(D.line_combos, t) for t in teams}

        sk = E.combine(_sum_windows([f.result() for f in f_sk]), f_psk.result())
        cur_g, prior_g = _sum_windows([f.result() for f in f_g]), f_pg.result()
        goalies = {}
        for pid in set(cur_g) | set(prior_g):
            c, p = cur_g.get(pid, {}), prior_g.get(pid, {})
            goalies[pid] = {k: c.get(k, 0) + E.PRIOR_SEASON_WEIGHT * p.get(k, 0) for k in ("sa", "ga", "gs")}
            goalies[pid]["name"] = (c or p)["name"]
        tsa, lg_sa = f_tsa.result()
        try:
            sg = f_sg.result()
        except Exception:
            sg = {}
        rosters = {}
        for t, f in f_rost.items():
            try:
                rosters[t] = f.result()
            except Exception:
                rosters[t] = []
        lines = {}
        for t, f in f_lines.items():
            try:
                lines[t] = f.result()
            except Exception:
                lines[t] = ({}, None)

    lc = E.league_constants(sk, goalies)
    cal = _cal()
    goalie_by_name = {D.norm_name(v["name"]): pid for pid, v in goalies.items()}
    global_names = {}
    for pid, v in sk.items():
        global_names.setdefault(D.norm_name(v["name"]), []).append(
            {"id": pid, "name": v["name"], "pos": v["pos"], "headshot": None})

    players, game_out = [], []
    for g in games:
        gi = {"game_id": g["game_id"], "away": g["away"], "home": g["home"], "start_utc": g["start_utc"],
              "state": g["state"], "venue": g["venue"], "goalies": {}}
        for team, opp, is_home in ((g["away"], g["home"], False), (g["home"], g["away"], True)):
            og = sg.get(opp, {})
            ogs = goalies.get(goalie_by_name.get(D.norm_name(og.get("name") or "")))
            gm, sv = E.goalie_mult(ogs, lc)
            gi["goalies"][opp] = {"name": og.get("name"), "status": og.get("status"),
                                  "sv_reg": round(sv, 4), "mult": round(gm, 3)}

            combo, updated = lines.get(team, ({}, None))
            by_name = {}
            for p in rosters.get(team, []):
                if p["pos"] != "G":
                    by_name.setdefault(D.norm_name(p["name"]), []).append(p)
            dressed = {k: c for k, c in combo.items() if c["line"] and c["line"][0] in "fd"}
            if not dressed:  # no lines published -> top 18 skaters by sample TOI
                ranked = sorted(by_name.values(), key=lambda ps: -sk.get(ps[0]["id"], {}).get("toi", 0))
                dressed = {D.norm_name(ps[0]["name"]): {"name": ps[0]["name"], "line": None, "pp": None, "gtd": False}
                           for ps in ranked[:18]}

            for key, c in dressed.items():
                rp = (_pick(by_name.get(key), c["line"]) or _pick(_fuzzy(key, by_name), c["line"])
                      or _pick(_fuzzy(key, global_names), c["line"]))
                pid = rp["id"] if rp else None
                pos = (rp or {}).get("pos") or ("D" if (c["line"] or "").startswith("d") else "C")
                v = sk.get(pid) if pid else None
                v = v or E.empty(pos)
                pr = E.predict(v, lc, line=c["line"], pp=c["pp"], opp_goalie=ogs,
                               opp_sa_pg=tsa.get(opp), league_sa_pg=lg_sa, home=is_home, cal=cal)
                players.append({
                    "game_id": g["game_id"], "player_id": pid, "name": c["name"], "team": team, "opp": opp,
                    "home": is_home, "pos": pos, "line": c["line"], "pp_unit": c["pp"],
                    "headshot": (rp or {}).get("headshot"),
                    "gp_sample": round(v["gp"], 1), "goals_sample": round(v["goals"], 1),
                    "p_goal": round(pr["p_goal"], 4), "fair_odds": E.fair_american(pr["p_goal"]),
                    "exp_shots": round(pr["exp_shots"], 2), "sh_pct_reg": round(pr["sh_pct_reg"], 4),
                    "proj_ev_min": round(pr["proj_ev_min"], 1), "proj_pp_min": round(pr["proj_pp_min"], 1),
                    "goalie_mult": round(pr["goalie_mult"], 3), "defense_mult": round(pr["defense_mult"], 3),
                    "matched": pid is not None, "gtd": c.get("gtd", False), "lines_updated": updated,
                })
        game_out.append(gi)
    players.sort(key=lambda p: -p["p_goal"])
    return {"date": date, "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "model": {"calibration": cal, "version": "goaliq-v1"}, "games": game_out, "players": players}


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    out = build(date)
    print(json.dumps(out, indent=1) if "--json" in sys.argv else f"{len(out['players'])} players")
