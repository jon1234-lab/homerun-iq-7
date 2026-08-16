"""
Real Statcast batter stats via `pybaseball`, which queries Baseball Savant's
public leaderboards -- no API key needed.

Design notes:

- The season leaderboard (barrel rate, hard-hit rate, exit velocity, xSLG) is
  fetched ONCE and cached for hours, then joined to roster players by their
  real MLBAM id. No per-player network calls.
- ID-based joins are used wherever a real roster id is available. Name-based
  lookup exists only for the mock-fallback roster, and tries several name
  variants because punctuated names (e.g. "C.J. Cron") don't always match the
  Chadwick register's formatting.
- Every step is wrapped in try/except. Savant's page structure and
  pybaseball's column names shift between versions; if anything fails we
  return None and the caller falls back to a clearly-labeled placeholder.
  The app should never crash because a scrape shape changed.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services import cache

try:
    import pybaseball as pyb

    pyb.cache.enable()
    _PYBASEBALL_AVAILABLE = True
except Exception as e:  # pragma: no cover
    print(f"[statcast] pybaseball unavailable, real batter stats disabled: {e}")
    _PYBASEBALL_AVAILABLE = False

# Used when a real player has no Statcast row yet (rookie call-up, very few
# batted balls). Labeled distinctly so the UI can flag it.
LEAGUE_AVERAGE_BATTER = {
    "barrel_rate": 0.08,
    "hard_hit_rate": 0.38,
    "xslg": 0.400,
    "exit_velocity": 88.5,
}


def current_season() -> int:
    return datetime.now(ZoneInfo("America/New_York")).year


def _fetch_season_batter_leaderboard(season: int) -> dict:
    """{mlbam_id: {barrel_rate, hard_hit_rate, xslg, exit_velocity}} for all
    batters with tracked batted balls this season. {} on any failure.

    Falls back to the previous season during the offseason / early spring,
    when the current season has no data yet.
    """
    cache_key = f"statcast_batters:{season}"
    cached = cache.get_cache(cache_key)
    if cached is not None:
        return {int(k): v for k, v in cached.items()}

    if not _PYBASEBALL_AVAILABLE:
        return {}

    lookup = {}
    try:
        ev_df = pyb.statcast_batter_exitvelo_barrels(season, 0)
        xstats_df = pyb.statcast_batter_expected_stats(season, 0)

        ev_df = ev_df.set_index("player_id")
        xstats_df = xstats_df.set_index("player_id")

        for player_id, row in ev_df.iterrows():
            try:
                pid = int(player_id)
                xslg = None
                if pid in xstats_df.index:
                    raw = xstats_df.loc[pid, "est_slg"]
                    if raw == raw:  # NaN check without importing numpy
                        xslg = float(raw)
                if xslg is None:
                    continue  # incomplete row -- skip rather than guess

                lookup[pid] = {
                    "barrel_rate": round(float(row["brl_percent"]) / 100.0, 4),
                    "hard_hit_rate": round(float(row["ev95percent"]) / 100.0, 4),
                    "xslg": round(xslg, 4),
                    "exit_velocity": round(float(row["avg_hit_speed"]), 2),
                }
            except Exception:
                continue  # skip one malformed row, keep the rest
    except Exception as e:
        print(f"[statcast] leaderboard fetch failed for {season}: {e}")
        return {}

    if not lookup:
        return {}

    cache.set_cache(cache_key, lookup, ttl_seconds=6 * 3600)
    print(f"[statcast] loaded {len(lookup)} real batter stat lines for {season}.")
    return lookup


def get_leaderboard(season: int | None = None) -> tuple[dict, int]:
    """Returns (leaderboard, season_actually_used). Tries the current season,
    then the previous one (covers offseason and the first days of a season)."""
    season = season or current_season()
    lb = _fetch_season_batter_leaderboard(season)
    if lb:
        return lb, season
    prev = season - 1
    lb = _fetch_season_batter_leaderboard(prev)
    if lb:
        return lb, prev
    return {}, season


def _lookup_mlbam_id(full_name: str) -> int | None:
    """Resolve a real MLBAM id from a name. Cached ~30 days. Only used for the
    mock-fallback roster; roster-driven paths use real ids directly."""
    cache_key = f"mlbam_id:{full_name.strip().lower()}"
    cached = cache.get_cache(cache_key)
    if cached is not None:
        return cached if cached != -1 else None

    if not _PYBASEBALL_AVAILABLE:
        return None

    parts = full_name.split()
    if len(parts) < 2:
        return None
    last = parts[-1]

    variants = [parts[0], parts[0].replace(".", "")]
    if len(parts) > 2:
        variants.append(" ".join(parts[:-1]).replace(".", " ").split()[0])

    seen = set()
    for first in variants:
        if not first or first in seen:
            continue
        seen.add(first)
        try:
            df = pyb.playerid_lookup(last, first)
            if df is not None and not df.empty:
                mlbam_id = int(df.iloc[0]["key_mlbam"])
                cache.set_cache(cache_key, mlbam_id, ttl_seconds=30 * 24 * 3600)
                return mlbam_id
        except Exception as e:
            print(f"[statcast] id lookup failed for '{first} {last}': {e}")

    try:
        df = pyb.playerid_lookup(last)
        if df is not None and len(df) == 1:
            mlbam_id = int(df.iloc[0]["key_mlbam"])
            cache.set_cache(cache_key, mlbam_id, ttl_seconds=30 * 24 * 3600)
            return mlbam_id
    except Exception:
        pass

    cache.set_cache(cache_key, -1, ttl_seconds=6 * 3600)
    return None


def get_real_batter_stats_by_id(mlbam_id: int, season: int | None = None) -> dict | None:
    """Preferred entrypoint: direct cached dict lookup by real MLBAM id."""
    leaderboard, _ = get_leaderboard(season)
    return leaderboard.get(mlbam_id)


def get_real_batter_stats(full_name: str, season: int | None = None) -> dict | None:
    """Name-based entrypoint, used only for the mock-fallback roster."""
    mlbam_id = _lookup_mlbam_id(full_name)
    if mlbam_id is None:
        return None
    return get_real_batter_stats_by_id(mlbam_id, season)


def statcast_enabled() -> bool:
    return _PYBASEBALL_AVAILABLE
