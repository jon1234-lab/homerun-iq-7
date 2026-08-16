from fastapi import APIRouter, Depends, Query

from app.database import save_prediction
from app.services import cache, hri_engine, mlb_data, weather
from app.services.plan_access import require_plan

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

PREV_SCORE_PREFIX = "prev_score:"
DEFAULT_LIMIT = 50
REAL_SOURCES = {"real_statcast"}


def _aggregate_source(sources: list[str]) -> str:
    if not sources:
        return "mock"
    real = sum(1 for s in sources if s in REAL_SOURCES)
    if real == len(sources):
        return "live"
    if real == 0:
        return "mock"
    return "mixed"


def _build_predictions(persist: bool = True) -> dict:
    matchups = mlb_data.get_matchups_for_today()
    results = []
    games_source = "mock"
    batter_sources = []
    weather_sources = []

    for m in matchups:
        games_source = m["data_source"]
        batter = mlb_data.get_batter_stats(m["batter"], m["data_source"])
        batter_sources.append(batter.get("stats_source", "mock"))
        pitcher = m["pitcher"]

        wx = weather.get_current_weather(m["park_team_name"], m["park_team_key"])
        weather_sources.append(wx["source"])

        environment = {
            "wind": wx["wind"],
            "temperature": wx["temperature"],
            "park_factor": m["park_factor"],
        }
        hri = hri_engine.calculate_hri_from_dicts(batter, pitcher, environment)

        cache_key = f"{PREV_SCORE_PREFIX}{batter['id']}"
        prev = cache.get_cache(cache_key)
        trend = round(hri["hri_score"] - prev, 1) if prev is not None else 0.0
        cache.set_cache(cache_key, hri["hri_score"], ttl_seconds=3600)

        results.append(
            {
                "player_id": batter["id"],
                "player_name": batter["name"],
                "team": batter["team"],
                "game_id": m["game_id"],
                "game_status": m["status"],
                "opponent_pitcher": pitcher["name"],
                "hri_score": hri["hri_score"],
                "hr_probability": hri["hr_probability"],
                "trend": trend,
                "park": m["park"],
                "wind": wx["wind"],
                "temperature": wx["temperature"],
                "park_factor": m["park_factor"],
                "batter_stats_source": batter.get("stats_source", "mock"),
                "pitcher_stats_source": pitcher.get("hr9_source", "mock"),
                "weather_source": wx["source"],
            }
        )

    results.sort(key=lambda r: r["hri_score"], reverse=True)

    # Persist only the top slice -- writing several hundred rows on every
    # 10-second websocket tick would be wasteful and pointless.
    if persist:
        for record in results[:DEFAULT_LIMIT]:
            save_prediction(record)

    return {
        "predictions": results,
        "data_source": games_source,
        "batter_data_source": _aggregate_source(batter_sources),
        "weather_source": "live" if any(s == "open_meteo" for s in weather_sources) else "simulated",
        "total_matchups": len(results),
    }


@router.get("/today")
def get_todays_predictions(limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000)):
    built = _build_predictions()
    sliced = built["predictions"][:limit]
    return {
        "count": len(sliced),
        "total_matchups": built["total_matchups"],
        "data_source": built["data_source"],
        "batter_data_source": built["batter_data_source"],
        "weather_source": built["weather_source"],
        "predictions": sliced,
    }


@router.get("/today/detailed", dependencies=[Depends(require_plan("elite"))])
def get_todays_predictions_detailed(limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000)):
    """Elite+ only: full HRI component breakdown (batter power / pitcher
    weakness / environment). Requires header X-User-Id for a user on the
    'elite' or 'edge' plan."""
    matchups = mlb_data.get_matchups_for_today()
    detailed = []
    games_source = "mock"
    batter_sources = []

    for m in matchups:
        games_source = m["data_source"]
        batter = mlb_data.get_batter_stats(m["batter"], m["data_source"])
        batter_sources.append(batter.get("stats_source", "mock"))
        pitcher = m["pitcher"]
        wx = weather.get_current_weather(m["park_team_name"], m["park_team_key"])
        hri = hri_engine.calculate_hri_from_dicts(
            batter,
            pitcher,
            {"wind": wx["wind"], "temperature": wx["temperature"], "park_factor": m["park_factor"]},
        )
        detailed.append(
            {
                "player_id": batter["id"],
                "player_name": batter["name"],
                "team": batter["team"],
                "opponent_pitcher": pitcher["name"],
                "batter_stats_source": batter.get("stats_source"),
                "pitcher_hr9_source": pitcher.get("hr9_source"),
                "pitcher_flyball_source": pitcher.get("flyball_source"),
                "weather_source": wx["source"],
                "raw_inputs": {
                    "barrel_rate": batter["barrel_rate"],
                    "hard_hit_rate": batter["hard_hit_rate"],
                    "xslg": batter["xslg"],
                    "exit_velocity": batter["exit_velocity"],
                    "hr_per9": pitcher["hr_per9"],
                    "flyball_rate": pitcher["flyball_rate"],
                    "wind": wx["wind"],
                    "temperature": wx["temperature"],
                    "park_factor": m["park_factor"],
                },
                **hri,
            }
        )

    detailed.sort(key=lambda r: r["hri_score"], reverse=True)
    return {
        "count": len(detailed[:limit]),
        "total_matchups": len(detailed),
        "data_source": games_source,
        "batter_data_source": _aggregate_source(batter_sources),
        "predictions": detailed[:limit],
    }
