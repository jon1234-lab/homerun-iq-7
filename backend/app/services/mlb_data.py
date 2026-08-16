"""
MLB data service -- the real-data backbone.

Everything here comes from the free, public MLB Stats API
(https://statsapi.mlb.com), no API key required:
  - today's real schedule, venues, and game status
  - real probable starting pitchers
  - real active rosters (position players; pitchers excluded)
  - real pitcher HR/9 and flyball rate from real season stats

Batter Statcast metrics come from app/services/statcast.py, joined by real
MLBAM id. Weather comes from app/services/weather.py (Open-Meteo, no key).

If the live API is unreachable (offseason, no network, bot-protection block),
everything falls back to a small deterministic mock slate so the app stays up.
The "data_source" / "batter_data_source" fields on every API response say
exactly which mode is active, down to the individual player.
"""
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from app.data import mock_data as md
from app.services import cache, statcast

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
REQUEST_TIMEOUT = 8

# statsapi.mlb.com sits behind bot-protection that rejects requests with no
# User-Agent or an obviously non-browser one. A browser-style UA avoids
# spurious 403s -- a real quirk of the service, not specific to this app.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

NAME_TO_ABBR = {t["name"]: t["id"] for t in md.TEAMS}
PITCHER_POSITION_ABBR = "P"

LEAGUE_AVG_HR9 = 1.2
LEAGUE_AVG_FLYBALL = 0.35


def _jitter(value: float, pct: float = 0.03, seed_key: str = "") -> float:
    """Small deterministic jitter per 10-second bucket, so all clients see the
    same 'live' value at the same instant without a shared write per tick."""
    bucket = int(time.time() // 10)
    rnd = random.Random(f"{seed_key}-{bucket}")
    return round(value + value * pct * (rnd.random() * 2 - 1), 4)


def _todays_date_str() -> str:
    # MLB's "game day" runs on Eastern time, not UTC or server local time.
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _parse_innings_pitched(ip_str: str) -> float:
    """MLB reports IP like '123.1' / '123.2' where the decimal digit is OUTS
    (0, 1, or 2), not a fraction."""
    try:
        if "." in str(ip_str):
            whole, outs = str(ip_str).split(".")
            return int(whole) + int(outs) / 3.0
        return float(ip_str)
    except (ValueError, AttributeError):
        return 0.0


def _fetch_json(url: str, cache_key: str, ttl: int):
    cached = cache.get_cache(cache_key)
    if cached is not None:
        return cached
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=REQUEST_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    cache.set_cache(cache_key, data, ttl_seconds=ttl)
    return data


def _fetch_real_pitcher_stats(pitcher_id: int, season: int) -> dict | None:
    """Real HR/9 AND real flyball rate from real season stats. Returns None if
    the pitcher has no innings yet."""
    url = (
        f"{MLB_API_BASE}/people/{pitcher_id}/stats"
        f"?stats=season&group=pitching&season={season}"
    )
    try:
        data = _fetch_json(url, f"pitcher:{pitcher_id}:{season}", ttl=3600)
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        stat = splits[0]["stat"]

        innings = _parse_innings_pitched(stat.get("inningsPitched", "0"))
        if innings <= 0:
            return None
        home_runs = float(stat.get("homeRuns", 0))
        hr_per9 = round((home_runs / innings) * 9, 3)

        # Real flyball rate = flyOuts / total balls in play, when available.
        fly_outs = float(stat.get("flyOuts", 0))
        ground_outs = float(stat.get("groundOuts", 0))
        balls_in_play = fly_outs + ground_outs
        flyball_rate = (
            round(fly_outs / balls_in_play, 4) if balls_in_play > 0 else LEAGUE_AVG_FLYBALL
        )
        flyball_source = "real_season_stats" if balls_in_play > 0 else "league_average"

        return {
            "hr_per9": hr_per9,
            "flyball_rate": flyball_rate,
            "hr9_source": "real_season_stats",
            "flyball_source": flyball_source,
        }
    except Exception as e:
        print(f"[mlb_data] pitcher stats fetch failed for {pitcher_id}: {e}")
        return None


def _resolve_pitcher(pitcher_info: dict | None, team_label: str, season: int, source: str) -> dict:
    if pitcher_info is None:
        return {
            "id": "unannounced",
            "name": "TBD",
            "team": team_label,
            "hr_per9": LEAGUE_AVG_HR9,
            "flyball_rate": LEAGUE_AVG_FLYBALL,
            "hr9_source": "league_average_placeholder",
            "flyball_source": "league_average_placeholder",
        }

    if source == "mock":
        p = md.get_pitcher(pitcher_info["id"])
        return {
            "id": p["id"],
            "name": p["name"],
            "team": p["team"],
            "hr_per9": _jitter(p["hr_per9"], 0.03, p["id"]),
            "flyball_rate": _jitter(p["flyball_rate"], 0.02, p["id"] + "fb"),
            "hr9_source": "mock",
            "flyball_source": "mock",
        }

    pitcher_id = pitcher_info["id"]
    real = _fetch_real_pitcher_stats(pitcher_id, season)
    if real:
        return {"id": str(pitcher_id), "name": pitcher_info["fullName"], "team": team_label, **real}

    return {
        "id": str(pitcher_id),
        "name": pitcher_info["fullName"],
        "team": team_label,
        "hr_per9": LEAGUE_AVG_HR9,
        "flyball_rate": LEAGUE_AVG_FLYBALL,
        "hr9_source": "league_average_placeholder",
        "flyball_source": "league_average_placeholder",
    }


def _fetch_team_roster_batters(team_mlb_id: int, team_label: str) -> list[dict]:
    """Real active roster, position players only. Cached 6h."""
    if not team_mlb_id:
        return []
    cache_key = f"roster:{team_mlb_id}"
    cached = cache.get_cache(cache_key)
    if cached is not None:
        return cached

    url = f"{MLB_API_BASE}/teams/{team_mlb_id}/roster?rosterType=active"
    try:
        data = _fetch_json(url, cache_key + ":raw", ttl=6 * 3600)
    except Exception as e:
        print(f"[mlb_data] roster fetch failed for team {team_mlb_id}: {e}")
        return []

    batters = []
    for entry in data.get("roster", []):
        try:
            if entry.get("position", {}).get("abbreviation") == PITCHER_POSITION_ABBR:
                continue
            person = entry["person"]
            batters.append({"id": str(person["id"]), "name": person["fullName"], "team": team_label})
        except Exception:
            continue

    cache.set_cache(cache_key, batters, ttl_seconds=6 * 3600)
    return batters


def _fetch_live_slate() -> list[dict] | None:
    date_str = _todays_date_str()
    url = f"{MLB_API_BASE}/schedule?sportId=1&date={date_str}&hydrate=probablePitcher,team,venue"

    try:
        data = _fetch_json(url, f"schedule:{date_str}", ttl=180)
    except Exception as e:
        print(f"[mlb_data] live schedule fetch failed, falling back to mock: {e}")
        return None

    dates = data.get("dates", [])
    if not dates:
        return []  # valid response, genuinely no games today

    season = int(date_str[:4])
    games = []
    for raw in dates[0].get("games", []):
        try:
            home = raw["teams"]["home"]
            away = raw["teams"]["away"]
            home_name = home["team"]["name"]
            away_name = away["team"]["name"]

            games.append(
                {
                    "game_id": str(raw["gamePk"]),
                    "home_team": home_name,
                    "home_team_id": NAME_TO_ABBR.get(home_name),
                    "home_team_mlb_id": home["team"]["id"],
                    "away_team": away_name,
                    "away_team_id": NAME_TO_ABBR.get(away_name),
                    "away_team_mlb_id": away["team"]["id"],
                    "park": raw.get("venue", {}).get("name", "Unknown Park"),
                    "park_factor": md.park_factor_for_team_name(home_name),
                    "game_time": raw.get("gameDate"),
                    "status": raw.get("status", {}).get("detailedState", "Scheduled"),
                    "probable_pitcher_home": home.get("probablePitcher"),
                    "probable_pitcher_away": away.get("probablePitcher"),
                    "season": season,
                }
            )
        except Exception as e:
            print(f"[mlb_data] skipping malformed game entry: {e}")
            continue

    return games


def _mock_slate() -> list[dict]:
    games = []
    for g in md.GAMES:
        home = md.get_team(g["home_team"])
        away = md.get_team(g["away_team"])
        games.append(
            {
                "game_id": g["game_id"],
                "home_team": home["name"],
                "home_team_id": home["id"],
                "home_team_mlb_id": None,
                "away_team": away["name"],
                "away_team_id": away["id"],
                "away_team_mlb_id": None,
                "park": home["park"],
                "park_factor": home["park_factor"],
                "game_time": None,
                "status": "Scheduled",
                "probable_pitcher_home": {
                    "id": g["starting_pitcher_home"],
                    "fullName": md.get_pitcher(g["starting_pitcher_home"])["name"],
                },
                "probable_pitcher_away": {
                    "id": g["starting_pitcher_away"],
                    "fullName": md.get_pitcher(g["starting_pitcher_away"])["name"],
                },
                "season": None,
            }
        )
    return games


def _get_slate() -> tuple[list[dict], str]:
    """Returns (games, data_source) -- 'live' or 'mock'."""
    live = _fetch_live_slate()
    if live:
        return live, "live"
    if live == []:
        # Real API reachable, genuinely no games scheduled today (off day /
        # offseason). Show the mock slate so the app still demonstrates value,
        # clearly labeled as such.
        print("[mlb_data] no real games scheduled today; showing demo slate.")
        return _mock_slate(), "mock"
    return _mock_slate(), "mock"


def get_todays_games() -> list[dict]:
    games, source = _get_slate()
    out = []
    for g in games:
        hp, ap = g["probable_pitcher_home"], g["probable_pitcher_away"]
        out.append(
            {
                "game_id": g["game_id"],
                "home_team": g["home_team"],
                "home_team_id": g["home_team_id"],
                "away_team": g["away_team"],
                "away_team_id": g["away_team_id"],
                "park": g["park"],
                "park_factor": g["park_factor"],
                "starting_pitcher_home": hp["fullName"] if hp else "TBD",
                "starting_pitcher_away": ap["fullName"] if ap else "TBD",
                "game_time": g.get("game_time"),
                "status": g["status"],
                "data_source": source,
            }
        )
    return out


def get_batter_stats(batter_ref: dict, data_source: str) -> dict:
    """batter_ref is {'id','name','team'} -- a real MLBAM id in live mode, or
    a curated mock id ('p001') in fallback mode."""
    season = int(_todays_date_str()[:4])

    if data_source == "live":
        try:
            real = statcast.get_real_batter_stats_by_id(int(batter_ref["id"]), season)
        except (ValueError, TypeError):
            real = None

        if real:
            return {**batter_ref, **real, "stats_source": "real_statcast"}

        # Real player with no Statcast row yet -- use a labeled league-average
        # placeholder rather than inventing a specific number.
        avg = statcast.LEAGUE_AVERAGE_BATTER
        return {
            **batter_ref,
            "barrel_rate": _jitter(avg["barrel_rate"], 0.05, batter_ref["id"]),
            "hard_hit_rate": _jitter(avg["hard_hit_rate"], 0.05, batter_ref["id"] + "hh"),
            "xslg": _jitter(avg["xslg"], 0.03, batter_ref["id"] + "xslg"),
            "exit_velocity": _jitter(avg["exit_velocity"], 0.02, batter_ref["id"] + "ev"),
            "stats_source": "league_average_placeholder",
        }

    # Mock slate: still try real Statcast by name (works when the schedule
    # fetch specifically failed but Savant is reachable).
    b = md.get_batter(batter_ref["id"])
    real = statcast.get_real_batter_stats(b["name"], season)
    if real:
        return {"id": b["id"], "name": b["name"], "team": b["team"], **real, "stats_source": "real_statcast"}

    return {
        "id": b["id"],
        "name": b["name"],
        "team": b["team"],
        "barrel_rate": _jitter(b["barrel_rate"], 0.02, b["id"]),
        "hard_hit_rate": _jitter(b["hard_hit_rate"], 0.02, b["id"] + "hh"),
        "xslg": _jitter(b["xslg"], 0.01, b["id"] + "xslg"),
        "exit_velocity": _jitter(b["exit_velocity"], 0.01, b["id"] + "ev"),
        "stats_source": "mock",
    }


def get_player_detail(player_id: str) -> dict | None:
    """Detail for any player: real MLBAM id (live Statcast) or curated mock id."""
    season = int(_todays_date_str()[:4])

    # Real MLBAM ids are numeric.
    if player_id.isdigit():
        real = statcast.get_real_batter_stats_by_id(int(player_id), season)
        try:
            person = _fetch_json(
                f"{MLB_API_BASE}/people/{player_id}?hydrate=currentTeam",
                f"person:{player_id}",
                ttl=24 * 3600,
            )
            info = person["people"][0]
            name = info["fullName"]
            team_name = info.get("currentTeam", {}).get("name", "Unknown")
        except Exception as e:
            print(f"[mlb_data] person lookup failed for {player_id}: {e}")
            name, team_name = f"Player {player_id}", "Unknown"

        if real:
            return {
                "id": player_id,
                "name": name,
                "team": team_name,
                "team_name": team_name,
                "park": "-",
                "park_factor": md.park_factor_for_team_name(team_name),
                **real,
                "stats_source": "real_statcast",
            }
        return None

    try:
        batter = md.get_batter(player_id)
    except StopIteration:
        return None
    team = md.get_team(batter["team"])
    stats = get_batter_stats(
        {"id": batter["id"], "name": batter["name"], "team": batter["team"]}, "mock"
    )
    return {**stats, "team_name": team["name"], "park": team["park"], "park_factor": team["park_factor"]}


def get_matchups_for_today() -> list[dict]:
    """Every batter-vs-opposing-starter matchup on today's slate.

    Live mode pulls each playing team's real active roster, so predictions
    scale to however many real games are on today -- not a fixed list.
    """
    games, source = _get_slate()
    season = int(_todays_date_str()[:4])
    matchups = []

    for g in games:
        game_season = g["season"] or season

        if source == "live":
            home_batters = _fetch_team_roster_batters(g["home_team_mlb_id"], g["home_team"])
            away_batters = _fetch_team_roster_batters(g["away_team_mlb_id"], g["away_team"])
        else:
            home_batters = [
                {"id": b["id"], "name": b["name"], "team": b["team"]}
                for b in (md.batters_for_team(g["home_team_id"]) if g["home_team_id"] else [])
            ]
            away_batters = [
                {"id": b["id"], "name": b["name"], "team": b["team"]}
                for b in (md.batters_for_team(g["away_team_id"]) if g["away_team_id"] else [])
            ]

        common = {
            "game_id": g["game_id"],
            "park": g["park"],
            "park_factor": g["park_factor"],
            "park_team_name": g["home_team"],   # weather is always the home park
            "park_team_key": g["home_team_id"],
            "status": g["status"],
            "data_source": source,
        }

        if home_batters:
            pitcher = _resolve_pitcher(g["probable_pitcher_away"], g["away_team"], game_season, source)
            for b in home_batters:
                matchups.append({**common, "batter": b, "pitcher": pitcher})

        if away_batters:
            pitcher = _resolve_pitcher(g["probable_pitcher_home"], g["home_team"], game_season, source)
            for b in away_batters:
                matchups.append({**common, "batter": b, "pitcher": pitcher})

    return matchups
