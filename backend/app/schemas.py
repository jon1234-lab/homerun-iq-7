from pydantic import BaseModel
from typing import Optional


class PredictionOut(BaseModel):
    player_id: str
    player_name: str
    team: str
    game_id: str
    opponent_pitcher: str
    hri_score: float
    hr_probability: float
    trend: float
    park: str
    wind: float
    temperature: float
    park_factor: float
    batter_stats_source: str


class GameOut(BaseModel):
    game_id: str
    home_team: str
    away_team: str
    park: str
    park_factor: float
    starting_pitcher_home: str
    starting_pitcher_away: str
    status: str
    data_source: str


class PlayerDetailOut(BaseModel):
    id: str
    name: str
    team: str
    team_name: str
    park: str
    park_factor: float
    barrel_rate: float
    hard_hit_rate: float
    xslg: float
    exit_velocity: float
    stats_source: str


class CheckoutSessionRequest(BaseModel):
    plan: str  # "pro" | "elite" | "edge"
    user_id: str
    email: Optional[str] = None


class CheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str
