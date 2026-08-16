from fastapi import APIRouter
from app.database import save_game_state
from app.services import mlb_data

router = APIRouter(prefix="/api/games", tags=["games"])


@router.get("/today")
def get_todays_games():
    games = mlb_data.get_todays_games()
    data_source = games[0]["data_source"] if games else "mock"
    for g in games:
        save_game_state(g)
    return {"count": len(games), "data_source": data_source, "games": games}
