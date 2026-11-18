"""The engine loop: refresh data, grade finished games, post bets ~3.5h before each game, replay the season
with historical odds once (marked 'backfill'), and keep model records current.

Everything runs inside the web service on a background thread (see web/main.py)."""
import datetime as dt
import threading
import time
import traceback
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import bets as B
from . import data as D
from . import odds as O
from . import project as P
from . import research as R
from . import store

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
POST_LEAD = dt.timedelta(hours=3, minutes=30)
BACKFILL_RESERVE = 2500       # never let the replay eat the credits live posting needs
LIVE_PROP_MIN = 60


def log(*a):
    print(time.strftime("%H:%M:%S"), "[engine]", *a, flush=True)


def et_today(now=None):
    return (now or dt.datetime.now(UTC)).astimezone(ET).date().isoformat()


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def snap5(t):
    return t.replace(second=0, microsecond=0, minute=t.minute - t.minute % 5)


class Engine:
    def __init__(self):
        self.lock = threading.Lock()
        self.sg = self.tg = self.gg = None
        self.vs_hist = D.vs_history()
        self.loaded = 0
        self.form = {}
        self.board = {}        # date -> {"games": [...], "players": [...], "built": ts}
        self.status = {"started": iso(dt.datetime.now(UTC)), "last_tick": None, "last_error": None, "credits": None}
        self.odds = O.Client()

    # ---------------------------------------------------------------- data
    def refresh(self, force=False):
        if not force and self.sg is not None and time.time() - self.loaded < 1800:
            return
        sg, tg, gg = D.load_all()
        self.sg, self.tg, self.gg = sg, tg, gg
        self.form = R.form(sg)
        cur = int(sg.season.max())
        active = set(sg.loc[sg.season == cur, "playerId"])
        sf, gx = P.compute(sg[sg.playerId.isin(active)], tg, gg)
        self.drift = P.next_drift(sf[sf.season == cur])
        keep = ["playerId", "name", "pos", "team", "opp", "home", "gameId", "date", "season", "mu_shots", "mu_goals",
                "mu_points", "p_goal", "p_point", "p_2pts", "p_sog2", "p_sog3", "p_sog4", "opp_goalie"]
        self.sf_all = sf.loc[sf.season == cur, keep].copy()
        self.gx_all = gx[gx.gameId.isin(tg[tg.season == cur].gameId)].copy()
        del sf, gx
        import gc
        gc.collect()
        self.loaded = time.time()
        self.board = {}
        log("data", len(sg), "skater-games through", sg.date.max().date())

    def vs_lookup(self, before):
        cur = self.sg[(self.sg.season == self.sg.season.max()) & (self.sg.date < pd.Timestamp(before))]
        add = cur.groupby(["playerId", "opp"]).agg(gp=("gameId", "size"), pts=("points", "sum"),
                                                   gw_pt=("points", lambda x: (x > 0).sum())).reset_index()
        allv = pd.concat([self.vs_hist[["playerId", "opp", "gp", "pts", "gw_pt"]], add]).groupby(
            ["playerId", "opp"]).sum().reset_index()
        return {(int(r.playerId), r.opp): {"gp": int(r.gp), "pts": int(r.pts), "gw_pt": int(r.gw_pt)}
                for r in allv.itertuples()}

    # ---------------------------------------------------------------- tonight's projections (no odds)
    def tonight(self, date, games=None, dfo=True):
        full = games is None
        b = self.board.get(date)
        if b and time.time() - b["built"] < 1200 and full:
            return b
        games = games if games is not None else D.schedule(date)
        if full and self.sg is not None and pd.Timestamp(date) <= self.sg.date.max():
            return self._past_board(date, games)
        if not games:
            return {"games": [], "players": pd.DataFrame(), "built": time.time(), "lineups": {}}
        teams = [t for g in games for t in (g["home"], g["away"])]
        recent = self.sg[(self.sg.date >= pd.Timestamp(date) - pd.Timedelta(days=30)) & (self.sg.date < pd.Timestamp(date))]
        recent = recent.sort_values("date").drop_duplicates("playerId", keep="last")[["playerId", "name", "pos", "team"]]
        recent = recent[recent.team.isin(teams)]
        lineups = {}
        if dfo:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(8) as ex:
                for t, (lc, _) in zip(teams, ex.map(D.line_combos, teams)):
                    lineups[t] = lc
            keep = []
            for r in recent.itertuples():
                lc = lineups.get(r.team) or {}
                rec = lc.get(O.norm(r.name))
                has_lines = any(v["line"] for v in lc.values())
                if rec and rec["injured"]:
                    continue
                if has_lines and not (rec and rec["line"]):
                    continue
                keep.append(r.Index)
            if keep:
                recent = recent.loc[keep]
        starters = P.likely_starters(self.gg, teams, date)
        confirmed = {}
        if dfo:
            sgs = D.starting_goalies(date)
            for t, v in sgs.items():
                gid = self._goalie_id(t, v["name"])
                if gid:
                    starters[t] = (gid, v["name"])
                    confirmed[t] = v.get("status")
        G = [{"game_id": g["game_id"], "home": g["home"], "away": g["away"]} for g in games]
        mine = self.sg[self.sg.playerId.isin(set(recent.playerId))]
        S, T, GG = P.append_tonight(mine, self.tg, self.gg, date, G, recent, starters)
        sf, gx = P.compute(S, T, GG, drift=self.drift)
        tn = sf[sf.date == pd.Timestamp(date)].copy()
        start = {g["game_id"]: g["start_utc"] for g in games}
        tn["start_utc"] = tn.gameId.map(start)
        if dfo:
            def ln(r):
                rec = (lineups.get(r.team) or {}).get(O.norm(r["name"])) or {}
                return pd.Series({"line": rec.get("line"), "pp": rec.get("pp"), "gtd": bool(rec.get("gtd"))})
            tn = pd.concat([tn, tn.apply(ln, axis=1)], axis=1) if len(tn) else tn
        gt = P.game_table(gx, [g["game_id"] for g in games])
        for g in gt:
            src = next(x for x in games if x["game_id"] == g["game_id"])
            g.update(start_utc=src["start_utc"], state=src.get("state"),
                     home_status=confirmed.get(g["home"]), away_status=confirmed.get(g["away"]))
        out = {"games": gt, "players": tn, "built": time.time(), "lineups": {}}
        if full:
            self.board[date] = out
        return out

    def _past_board(self, date, games):
        """A date already played: projections as they stood before puck drop (actual lineups/starters)."""
        start = {g["game_id"]: g["start_utc"] for g in games}
        tn = self.sf_all[(self.sf_all.date == pd.Timestamp(date)) & self.sf_all.gameId.isin(start)].copy()
        tn["start_utc"] = tn.gameId.map(start)
        gt = P.game_table(self.gx_all, list(start))
        for g in gt:
            src = next(x for x in games if x["game_id"] == g["game_id"])
            g.update(start_utc=src["start_utc"], state=src.get("state"), home_status="Played", away_status="Played",
                     home_score=src.get("home_score"), away_score=src.get("away_score"))
        out = {"games": gt, "players": tn, "built": time.time(), "lineups": {}}
        self.board[date] = out
        return out

    def _goalie_id(self, team, name):
        k = O.norm(name)
        g = self.gg[self.gg.name.map(O.norm) == k].sort_values("date")
        if len(g):
            t = g[g.team == team]
            return int((t if len(t) else g).playerId.iloc[-1])
        return None

    # ---------------------------------------------------------------- grading
    def grade_pending(self, today):
        frm = (pd.Timestamp(today) - pd.Timedelta(days=5)).date().isoformat()
        pend = [b for b in store.get_bets(frm, today) if b["result"] == "pending"]
        if not pend:
            return 0
        gids = {b["game_id"] for b in pend} | {l["game_id"] for b in pend if b.get("legs") for l in b["legs"]}
        finals, players = {}, {}
        for gid in gids:
            try:
                bx = D.boxscore(gid)
            except Exception:
                continue
            if bx["state"] in ("FINAL", "OFF"):
                finals[gid] = (bx["home"], bx["away"], bx["home_score"], bx["away_score"])
                players.update(bx["players"])
        upd = []
        for b in pend:
            for k in ("line", "p_model", "ev"):
                if b.get(k) is not None:
                    b[k] = float(b[k])
            g = B.grade(b, players, finals)
            if g:
                upd.append(g)
        if upd:
            store.put_bets(upd)
            log("graded", len(upd))
        return len(upd)

    # ---------------------------------------------------------------- live posting
    def post_due(self, now):
        date = et_today(now)
        games = D.schedule(date)
        posted = set(store.get_kv(f"posted:{date}").get(f"posted:{date}", []))
        due = [g for g in games if g["game_id"] not in posted and g["state"] in ("FUT", "PRE")
               and dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) - POST_LEAD <= now
               < dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) - dt.timedelta(minutes=5)]
        if not due:
            return 0
        board = self.tonight(date, games=due)
        offers = []
        if self.odds.ok():
            t0 = min(dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) for g in due)
            t1 = max(dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) for g in due)
            frm, to = iso(t0 - dt.timedelta(minutes=1)), iso(t1 + dt.timedelta(minutes=1))
            evs = self.odds.events(frm, to)
            for ev in self.odds.game_odds(frm, to):
                offers += O.parse_event(ev)
            for g in due:
                ev = next((e for e in evs if O.team_abbr(e["home_team"]) == g["home"]
                           and O.team_abbr(e["away_team"]) == g["away"]), None)
                if ev and (self.odds.remaining is None or self.odds.remaining > LIVE_PROP_MIN):
                    offers += O.parse_event(self.odds.event_props(ev["id"]))
            self.status["credits"] = self.odds.remaining
        best = O.best_prices(offers)
        cands = board["players"][["playerId", "name", "team"]] if len(board["players"]) else pd.DataFrame(
            columns=["playerId", "name", "team"])
        best = B.match_players(best, cands)
        bets = B.make_bets(date, board["players"], board["games"], best, self.vs_lookup(date), self.form, "live", iso(now))
        if bets:
            store.put_bets(bets)
        store.put_kv(f"posted:{date}", sorted(posted | {g["game_id"] for g in due}))
        self._save_slate(date, board, best)
        log("posted", len(bets), "bets for", len(due), "games; credits left", self.odds.remaining)
        return len(bets)

    def _save_slate(self, date, board, best):
        """Keep the prices we saw (for team trends vs the posted total and the props page)."""
        cur = store.get_kv(f"odds:{date}").get(f"odds:{date}", {})
        for o in best:
            if o["market"] in ("ml", "pl", "total"):
                gid = next((g["game_id"] for g in board["games"] if g["home"] == o["home"] and g["away"] == o["away"]), None)
                if gid:
                    cur.setdefault(str(gid), {}).setdefault(o["market"], []).append(
                        {"side": o["side"], "line": o["line"], "price": o["price"], "book": o["book"]})
            elif "player_id" in o:
                cur.setdefault("p" + str(o["player_id"]), []).append(
                    {"m": o["market"], "s": o["side"], "l": o["line"], "pr": o["price"], "b": o["book"]})
        store.put_kv(f"odds:{date}", cur)

    # ---------------------------------------------------------------- season replay with historical odds
    def backfill_step(self, now, max_dates=3):
        if not self.odds.ok():
            return 0
        st = store.get_kv("backfill").get("backfill", {"done": [], "failed": {}})
        cur = self.tg[self.tg.season == self.tg.season.max()]
        today = et_today(now)
        dates = sorted({d.date().isoformat() for d in cur.date if d.date().isoformat() < today})
        todo = [d for d in dates if d not in st["done"] and st["failed"].get(d, 0) < 3]
        n = 0
        for date in todo[:max_dates]:
            if self.odds.remaining is not None and self.odds.remaining < BACKFILL_RESERVE:
                log("backfill paused: credits", self.odds.remaining)
                break
            try:
                k = self._backfill_date(date)
                st["done"].append(date)
                n += k
            except Exception as e:
                st["failed"][date] = st["failed"].get(date, 0) + 1
                log("backfill failed", date, repr(e)[:200])
            store.put_kv("backfill", st)
        if n:
            log("backfill bets", n, "credits left", self.odds.remaining)
        self.status["credits"] = self.odds.remaining
        return n

    def _backfill_date(self, date):
        games = D.schedule(date)
        games = [g for g in games if g["state"] in ("FINAL", "OFF")]
        if not games:
            return 0
        day = self.sf_all[self.sf_all.date == pd.Timestamp(date)].copy()
        start = {g["game_id"]: g["start_utc"] for g in games}
        day = day[day.gameId.isin(start)]
        day["start_utc"] = day.gameId.map(start)
        gt = P.game_table(self.gx_all, list(start))
        for g in gt:
            g["start_utc"] = start[g["game_id"]]
        # snapshot = posting time (start - 3.5h); one game-lines pull per distinct posting window
        t = {g["game_id"]: snap5(dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) - POST_LEAD) for g in games}
        frm = iso(min(dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) for g in games) - dt.timedelta(hours=1))
        to = iso(max(dt.datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00")) for g in games) + dt.timedelta(hours=1))
        groups = {}
        for gid, s in t.items():
            key = next((k for k in groups if abs((k - s).total_seconds()) <= 3 * 3600), s)
            groups.setdefault(key, []).append(gid)
        offers = []
        ev_map = {}
        for snap, gids in groups.items():
            s_iso = iso(min(t[g] for g in gids))
            for ev in self.odds.hist_events(s_iso, frm, to):
                ev_map[(O.team_abbr(ev["home_team"]), O.team_abbr(ev["away_team"]))] = ev["id"]
            for ev in self.odds.hist_game_odds(s_iso, frm, to):
                offers += [o for o in O.parse_event(ev)]
        for g in games:
            eid = ev_map.get((g["home"], g["away"]))
            if eid:
                offers += O.parse_event(self.odds.hist_event_props(eid, iso(t[g["game_id"]])))
        best = O.best_prices(offers)
        best = B.match_players(best, day[["playerId", "name", "team"]])
        hist_form = R.form(self.sg[self.sg.date < pd.Timestamp(date)])
        bets = B.make_bets(date, day, gt, best, self.vs_lookup(date), hist_form, "backfill",
                           iso(min(t.values())))
        # grade right away from the box-score logs
        players = {int(r.playerId): {"goals": r.goals, "points": r.points, "sog": r.shots}
                   for r in self.sg[self.sg.date == pd.Timestamp(date)].itertuples()}
        tgd = self.tg[(self.tg.date == pd.Timestamp(date)) & (self.tg.home == 1)]
        finals = {int(r.gameId): (r.team, r.opp, int(r.gf), int(r.ga)) for r in tgd.itertuples()}
        graded = [B.grade(b, players, finals) or b for b in bets]
        if graded:
            store.put_bets(graded)
        self._save_slate(date, {"games": gt}, best)
        return len(graded)

    # ---------------------------------------------------------------- records
    def update_records(self, today):
        season_start = self.tg[self.tg.season == self.tg.season.max()].date.min().date().isoformat()
        allb = [b for b in store.get_bets(season_start, today) if b["model"] in B.MODELS or b["model"] in B.PARLAYS]
        for b in allb:
            b["units"] = float(b["units"]) if b.get("units") is not None else None
        rec = B.records(allb, today)
        rec["updated"] = iso(dt.datetime.now(UTC))
        rec["n_backfill"] = sum(1 for b in allb if b["source"] == "backfill")
        rec["n_live"] = sum(1 for b in allb if b["source"] == "live")
        store.put_kv("records", rec)
        return rec

    # ---------------------------------------------------------------- loop
    def tick(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            now = dt.datetime.now(UTC)
            today = et_today(now)
            self.refresh()
            changed = self.grade_pending(today)
            changed += self.post_due(now)
            changed += self.backfill_step(now)
            if changed or not self.status.get("last_tick"):
                self.update_records(today)
            try:
                self.tonight(today)
            except Exception as e:
                log("board error", repr(e)[:200])
            self.status["last_tick"] = iso(now)
            self.status["last_error"] = None
        except Exception as e:
            self.status["last_error"] = repr(e)[:300]
            log("tick error", traceback.format_exc()[-1500:])
        finally:
            self.lock.release()

    def loop(self, every=600):
        while True:
            self.tick()
            time.sleep(every)
