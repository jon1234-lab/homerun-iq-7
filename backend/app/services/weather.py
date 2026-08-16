"""
Weather service.

Real conditions via Open-Meteo (free, NO API KEY REQUIRED) when park
coordinates are known. Falls back to a deterministic simulated value if the
service is unreachable, so predictions never break.

Wind is signed relative to the batter: positive = blowing out to center
(helps home runs), negative = blowing in. We compute that from the real wind
direction and each park's center-field bearing.
"""
import math
import random
import time

import requests

from app.services import cache

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 6

# lat, lon, and the compass bearing from home plate toward center field.
# Wind blowing along that bearing carries balls out.
PARK_GEO = {
    "Arizona Diamondbacks": (33.4455, -112.0667, 0),
    "Atlanta Braves": (33.8907, -84.4677, 150),
    "Baltimore Orioles": (39.2839, -76.6217, 32),
    "Boston Red Sox": (42.3467, -71.0972, 45),
    "Chicago Cubs": (41.9484, -87.6553, 30),
    "Chicago White Sox": (41.8299, -87.6338, 5),
    "Cincinnati Reds": (39.0975, -84.5069, 60),
    "Cleveland Guardians": (41.4962, -81.6852, 0),
    "Colorado Rockies": (39.7559, -104.9942, 5),
    "Detroit Tigers": (42.3390, -83.0485, 30),
    "Houston Astros": (29.7572, -95.3555, 345),
    "Kansas City Royals": (39.0517, -94.4803, 45),
    "Los Angeles Angels": (33.8003, -117.8827, 40),
    "Los Angeles Dodgers": (34.0739, -118.2400, 25),
    "Miami Marlins": (25.7781, -80.2197, 40),
    "Milwaukee Brewers": (43.0280, -87.9712, 0),
    "Minnesota Twins": (44.9817, -93.2777, 60),
    "New York Mets": (40.7571, -73.8458, 25),
    "New York Yankees": (40.8296, -73.9262, 20),
    "Athletics": (39.5432, -119.7660, 30),
    "Oakland Athletics": (37.7516, -122.2005, 60),
    "Philadelphia Phillies": (39.9061, -75.1665, 20),
    "Pittsburgh Pirates": (40.4469, -80.0057, 120),
    "San Diego Padres": (32.7076, -117.1570, 0),
    "San Francisco Giants": (37.7786, -122.3893, 60),
    "Seattle Mariners": (47.5914, -122.3325, 60),
    "St. Louis Cardinals": (38.6226, -90.1928, 30),
    "Tampa Bay Rays": (27.7683, -82.6534, 45),
    "Texas Rangers": (32.7473, -97.0842, 0),
    "Toronto Blue Jays": (43.6414, -79.3894, 0),
    "Washington Nationals": (38.8730, -77.0074, 30),
}

# Simulated baselines for the small mock-fallback slate (keyed by abbreviation)
MOCK_WEATHER = {
    "NYY": {"wind": 6, "temperature": 78},
    "BOS": {"wind": 10, "temperature": 72},
    "LAD": {"wind": -2, "temperature": 74},
    "COL": {"wind": 14, "temperature": 85},
    "SF": {"wind": -8, "temperature": 62},
    "CHC": {"wind": 12, "temperature": 70},
    "HOU": {"wind": 3, "temperature": 88},
    "ATL": {"wind": 5, "temperature": 82},
}


def _signed_wind(speed_mph: float, wind_from_deg: float, cf_bearing_deg: float) -> float:
    """Project wind onto the home-plate -> center-field axis.

    Open-Meteo reports the direction the wind is coming FROM. Wind blowing
    toward center field (i.e. coming from behind home plate) is a positive
    'blowing out' value.
    """
    blowing_toward = (wind_from_deg + 180) % 360
    angle_diff = math.radians(blowing_toward - cf_bearing_deg)
    return round(speed_mph * math.cos(angle_diff), 1)


def get_real_weather(team_name: str) -> dict | None:
    """Real current conditions at a park, or None if unavailable."""
    geo = PARK_GEO.get(team_name)
    if not geo:
        return None

    lat, lon, cf_bearing = geo
    cache_key = f"weather:{team_name}"
    cached = cache.get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,wind_direction_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        current = resp.json()["current"]

        result = {
            "team_id": team_name,
            "wind": _signed_wind(
                float(current["wind_speed_10m"]),
                float(current["wind_direction_10m"]),
                cf_bearing,
            ),
            "temperature": round(float(current["temperature_2m"]), 1),
            "conditions": "live",
            "source": "open_meteo",
        }
        cache.set_cache(cache_key, result, ttl_seconds=900)  # 15 min
        return result
    except Exception as e:
        print(f"[weather] real fetch failed for {team_name}: {e}")
        return None


def get_simulated_weather(park_key: str) -> dict:
    base = MOCK_WEATHER.get(park_key, {"wind": 0, "temperature": 72})
    bucket = int(time.time() // 600)
    rnd = random.Random(f"weather-{park_key}-{bucket}")
    return {
        "team_id": park_key,
        "wind": round(base["wind"] + rnd.uniform(-2, 2), 1),
        "temperature": round(base["temperature"] + rnd.uniform(-1.5, 1.5), 1),
        "conditions": "simulated",
        "source": "simulated",
    }


def get_current_weather(team_name: str | None, park_key: str | None = None) -> dict:
    """Preferred entrypoint. Tries real weather by full team name, then falls
    back to a deterministic simulated value."""
    if team_name:
        real = get_real_weather(team_name)
        if real:
            return real
    return get_simulated_weather(park_key or team_name or "UNK")
