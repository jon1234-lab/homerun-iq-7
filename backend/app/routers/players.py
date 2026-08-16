from fastapi import APIRouter, HTTPException
from app.services import mlb_data

router = APIRouter(prefix="/api/player", tags=["players"])


@router.get("/{player_id}")
def get_player(player_id: str):
    player = mlb_data.get_player_detail(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found or no stats available")
    return player
