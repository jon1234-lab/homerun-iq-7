"""The Odds API client (live + historical) and parsing into flat offer rows.

Bets are priced at the best of the four big US books at posting time.
Credit costs: live game lines 3 per call (all games), live props 3 per game; historical x10.
"""
import os
import re
import time
import unicodedata

import requests

API = "https://api.the-odds-api.com/v4"
SPORT = "icehockey_nhl"
BOOKS = os.environ.get("ODDS_BOOKS", "draftkings,fanduel,betmgm,williamhill_us")
BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "williamhill_us": "Caesars"}
PROP_MARKETS = "player_goal_scorer_anytime,player_points,player_shots_on_goal"
GAME_MARKETS = "h2h,spreads,totals"
MK = {"player_goal_scorer_anytime": "goal", "player_points": "points", "player_shots_on_goal": "sog",
      "h2h": "ml", "spreads": "pl", "totals": "total"}

NICK = {"ducks": "ANA", "bruins": "BOS", "sabres": "BUF", "flames": "CGY", "hurricanes": "CAR", "blackhawks": "CHI",
        "avalanche": "COL", "jackets": "CBJ", "stars": "DAL", "wings": "DET", "oilers": "EDM", "panthers": "FLA",
        "kings": "LAK", "wild": "MIN", "canadiens": "MTL", "predators": "NSH", "devils": "NJD", "islanders": "NYI",
        "rangers": "NYR", "senators": "OTT", "flyers": "PHI", "penguins": "PIT", "sharks": "SJS", "kraken": "SEA",
        "blues": "STL", "lightning": "TBL", "leafs": "TOR", "mammoth": "UTA", "club": "UTA", "canucks": "VAN",
        "knights": "VGK", "capitals": "WSH", "jets": "WPG", "coyotes": "ARI"}


def team_abbr(full):
    w = norm(full).split()
    return NICK.get(w[-1]) if w else None


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s.replace("-", " "))
    return re.sub(r"\s+", " ", s).strip()


class OddsError(RuntimeError):
    pass


class Client:
    def __init__(self, key=None):
        self.key = key or os.environ.get("ODDS_API_KEY", "")
        self.remaining = None
        self.used_this_run = 0
        self.s = requests.Session()

    def ok(self):
        return bool(self.key)

    def _get(self, path, **params):
        if not self.key:
            raise OddsError("ODDS_API_KEY is not set")
        params = {k: v for k, v in params.items() if v is not None}
        for i in range(4):
            r = self.s.get(f"{API}{path}", params=params, headers={"x-api-key": self.key}, timeout=40)
            if r.status_code == 429:
                time.sleep(2 + 3 * i)
                continue
            break
        if r.headers.get("x-requests-remaining") is not None:
            try:
                self.remaining = int(float(r.headers["x-requests-remaining"]))
                self.used_this_run += int(float(r.headers.get("x-requests-last", 0)))
            except ValueError:
                pass
        if r.status_code == 422 or r.status_code == 404:
            return None
        if r.status_code != 200:
            raise OddsError(f"{r.status_code} {r.text[:200]}")
        return r.json()

    # ---------------- live
    def events(self, frm, to):
        return self._get(f"/sports/{SPORT}/events", commenceTimeFrom=frm, commenceTimeTo=to, dateFormat="iso") or []

    def game_odds(self, frm, to):
        return self._get(f"/sports/{SPORT}/odds", bookmakers=BOOKS, markets=GAME_MARKETS, oddsFormat="american",
                         commenceTimeFrom=frm, commenceTimeTo=to) or []

    def event_props(self, event_id):
        return self._get(f"/sports/{SPORT}/events/{event_id}/odds", bookmakers=BOOKS, markets=PROP_MARKETS,
                         oddsFormat="american")

    # ---------------- historical (paid plans)
    def hist_events(self, snapshot, frm, to):
        d = self._get(f"/historical/sports/{SPORT}/events", date=snapshot, commenceTimeFrom=frm, commenceTimeTo=to)
        return (d or {}).get("data") or []

    def hist_game_odds(self, snapshot, frm, to):
        d = self._get(f"/historical/sports/{SPORT}/odds", date=snapshot, bookmakers=BOOKS, markets=GAME_MARKETS,
                      oddsFormat="american", commenceTimeFrom=frm, commenceTimeTo=to)
        return (d or {}).get("data") or []

    def hist_event_props(self, event_id, snapshot):
        d = self._get(f"/historical/sports/{SPORT}/events/{event_id}/odds", date=snapshot, bookmakers=BOOKS,
                      markets=PROP_MARKETS, oddsFormat="american")
        return (d or {}).get("data")


def parse_event(ev):
    """Flatten one event (props and/or game lines) into offer rows."""
    rows = []
    if not ev:
        return rows
    home, away = team_abbr(ev.get("home_team")), team_abbr(ev.get("away_team"))
    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            mk = MK.get(m.get("key"))
            if not mk:
                continue
            for o in m.get("outcomes", []):
                price = o.get("price")
                if price is None:
                    continue
                row = {"event_id": ev.get("id"), "home": home, "away": away, "commence": ev.get("commence_time"),
                       "book": b.get("key"), "market": mk, "price": int(round(float(price))), "line": o.get("point")}
                name = o.get("name") or ""
                if mk in ("goal", "points", "sog"):
                    row["player"] = o.get("description") or ""
                    row["pkey"] = norm(row["player"])
                    side = name.lower()
                    row["side"] = "yes" if side == "yes" else ("no" if side == "no" else side)
                    if mk == "goal":
                        row["line"] = 0.5
                elif mk == "ml":
                    row["side"] = team_abbr(name)
                elif mk == "pl":
                    row["side"] = team_abbr(name)
                else:
                    row["side"] = name.lower()
                rows.append(row)
    return rows


def _dec(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def best_prices(rows):
    """Best price per (event, market, player/side, side, line) across books, plus how many books offer it."""
    best = {}
    for r in rows:
        k = (r["event_id"], r["market"], r.get("pkey"), r["side"], r["line"])
        cur = best.get(k)
        if cur is None:
            best[k] = dict(r, n_books=1)
        else:
            cur["n_books"] += 1
            if _dec(r["price"]) > _dec(cur["price"]):
                cur.update(price=r["price"], book=r["book"])
    return list(best.values())
