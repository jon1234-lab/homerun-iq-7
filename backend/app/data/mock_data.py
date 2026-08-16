"""
Fallback mock data, used ONLY when the real MLB Stats API is unreachable
(offseason, no network, rate limiting, bot-protection block, etc.).

In normal operation the app runs entirely on real data: real games, real
rosters, real Statcast batter stats. This module keeps the app functional
and honest -- everything sourced from here is labeled "mock" in the API
response and flagged in the UI.
"""

TEAMS = [
    {"id": "NYY", "name": "New York Yankees", "park": "Yankee Stadium", "park_factor": 1.12},
    {"id": "BOS", "name": "Boston Red Sox", "park": "Fenway Park", "park_factor": 1.05},
    {"id": "LAD", "name": "Los Angeles Dodgers", "park": "Dodger Stadium", "park_factor": 0.95},
    {"id": "COL", "name": "Colorado Rockies", "park": "Coors Field", "park_factor": 1.28},
    {"id": "SF", "name": "San Francisco Giants", "park": "Oracle Park", "park_factor": 0.82},
    {"id": "CHC", "name": "Chicago Cubs", "park": "Wrigley Field", "park_factor": 1.02},
    {"id": "HOU", "name": "Houston Astros", "park": "Daikin Park", "park_factor": 1.08},
    {"id": "ATL", "name": "Atlanta Braves", "park": "Truist Park", "park_factor": 1.01},
]

# Park factors for every MLB park, used to enrich real games with real venues.
# Keyed by the team name exactly as the MLB Stats API returns it.
PARK_FACTORS_BY_TEAM_NAME = {
    "Arizona Diamondbacks": 1.03,
    "Atlanta Braves": 1.01,
    "Baltimore Orioles": 1.06,
    "Boston Red Sox": 1.05,
    "Chicago Cubs": 1.02,
    "Chicago White Sox": 1.09,
    "Cincinnati Reds": 1.18,
    "Cleveland Guardians": 0.98,
    "Colorado Rockies": 1.28,
    "Detroit Tigers": 0.94,
    "Houston Astros": 1.08,
    "Kansas City Royals": 0.97,
    "Los Angeles Angels": 1.04,
    "Los Angeles Dodgers": 0.95,
    "Miami Marlins": 0.88,
    "Milwaukee Brewers": 1.07,
    "Minnesota Twins": 1.00,
    "New York Mets": 0.93,
    "New York Yankees": 1.12,
    "Athletics": 0.90,
    "Oakland Athletics": 0.90,
    "Philadelphia Phillies": 1.10,
    "Pittsburgh Pirates": 0.91,
    "San Diego Padres": 0.92,
    "San Francisco Giants": 0.82,
    "Seattle Mariners": 0.93,
    "St. Louis Cardinals": 0.96,
    "Tampa Bay Rays": 0.96,
    "Texas Rangers": 1.02,
    "Toronto Blue Jays": 1.06,
    "Washington Nationals": 1.01,
}

BATTERS = [
    {"id": "p001", "name": "Aaron Judge", "team": "NYY", "barrel_rate": 0.22, "hard_hit_rate": 0.58, "xslg": 0.660, "exit_velocity": 96.5},
    {"id": "p002", "name": "Shohei Ohtani", "team": "LAD", "barrel_rate": 0.20, "hard_hit_rate": 0.55, "xslg": 0.640, "exit_velocity": 95.8},
    {"id": "p003", "name": "Kyle Schwarber", "team": "BOS", "barrel_rate": 0.19, "hard_hit_rate": 0.50, "xslg": 0.560, "exit_velocity": 93.2},
    {"id": "p004", "name": "Matt Olson", "team": "ATL", "barrel_rate": 0.16, "hard_hit_rate": 0.48, "xslg": 0.540, "exit_velocity": 92.1},
    {"id": "p005", "name": "Kyle Tucker", "team": "HOU", "barrel_rate": 0.14, "hard_hit_rate": 0.45, "xslg": 0.520, "exit_velocity": 91.0},
    {"id": "p006", "name": "Ryan McMahon", "team": "COL", "barrel_rate": 0.13, "hard_hit_rate": 0.44, "xslg": 0.480, "exit_velocity": 90.3},
    {"id": "p007", "name": "Seiya Suzuki", "team": "CHC", "barrel_rate": 0.17, "hard_hit_rate": 0.47, "xslg": 0.500, "exit_velocity": 91.8},
    {"id": "p008", "name": "Matt Chapman", "team": "SF", "barrel_rate": 0.15, "hard_hit_rate": 0.43, "xslg": 0.490, "exit_velocity": 90.9},
    {"id": "p009", "name": "Rafael Devers", "team": "BOS", "barrel_rate": 0.18, "hard_hit_rate": 0.52, "xslg": 0.580, "exit_velocity": 94.0},
    {"id": "p010", "name": "Mookie Betts", "team": "LAD", "barrel_rate": 0.13, "hard_hit_rate": 0.42, "xslg": 0.510, "exit_velocity": 90.0},
]

PITCHERS = [
    {"id": "s001", "name": "Gerrit Cole", "team": "NYY", "hr_per9": 0.9, "flyball_rate": 0.34},
    {"id": "s002", "name": "Brayan Bello", "team": "BOS", "hr_per9": 1.3, "flyball_rate": 0.41},
    {"id": "s003", "name": "Yoshinobu Yamamoto", "team": "LAD", "hr_per9": 0.8, "flyball_rate": 0.32},
    {"id": "s004", "name": "Kyle Freeland", "team": "COL", "hr_per9": 1.6, "flyball_rate": 0.44},
    {"id": "s005", "name": "Logan Webb", "team": "SF", "hr_per9": 0.7, "flyball_rate": 0.30},
    {"id": "s006", "name": "Justin Steele", "team": "CHC", "hr_per9": 1.0, "flyball_rate": 0.35},
    {"id": "s007", "name": "Framber Valdez", "team": "HOU", "hr_per9": 0.6, "flyball_rate": 0.28},
    {"id": "s008", "name": "Chris Sale", "team": "ATL", "hr_per9": 1.1, "flyball_rate": 0.37},
]

GAMES = [
    {"game_id": "g1", "home_team": "COL", "away_team": "CHC", "starting_pitcher_home": "s004", "starting_pitcher_away": "s006"},
    {"game_id": "g2", "home_team": "NYY", "away_team": "BOS", "starting_pitcher_home": "s001", "starting_pitcher_away": "s002"},
    {"game_id": "g3", "home_team": "LAD", "away_team": "SF", "starting_pitcher_home": "s003", "starting_pitcher_away": "s005"},
    {"game_id": "g4", "home_team": "HOU", "away_team": "ATL", "starting_pitcher_home": "s007", "starting_pitcher_away": "s008"},
]

WEATHER_BY_PARK = {
    "NYY": {"wind": 6, "temperature": 78},
    "BOS": {"wind": 10, "temperature": 72},
    "LAD": {"wind": -2, "temperature": 74},
    "COL": {"wind": 14, "temperature": 85},
    "SF": {"wind": -8, "temperature": 62},
    "CHC": {"wind": 12, "temperature": 70},
    "HOU": {"wind": 3, "temperature": 88},
    "ATL": {"wind": 5, "temperature": 82},
}


def get_team(team_id: str) -> dict:
    return next(t for t in TEAMS if t["id"] == team_id)


def get_batter(player_id: str) -> dict:
    return next(b for b in BATTERS if b["id"] == player_id)


def get_pitcher(pitcher_id: str) -> dict:
    return next(p for p in PITCHERS if p["id"] == pitcher_id)


def batters_for_team(team_id: str) -> list[dict]:
    return [b for b in BATTERS if b["team"] == team_id]


def park_factor_for_team_name(team_name: str) -> float:
    """Real park factor for any MLB team by full name; 1.0 (neutral) if unknown."""
    return PARK_FACTORS_BY_TEAM_NAME.get(team_name, 1.0)
