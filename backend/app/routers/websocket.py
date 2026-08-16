import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.routers.predictions import DEFAULT_LIMIT, _build_predictions

router = APIRouter()
BROADCAST_INTERVAL_SECONDS = 10


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, text: str):
        stale = []
        for connection in self.active_connections:
            try:
                await connection.send_text(text)
            except Exception:
                stale.append(connection)
        for c in stale:
            self.disconnect(c)


manager = ConnectionManager()
_loop_started = False


def _snapshot_message() -> str:
    # Recomputing is cheap: rosters, Statcast, and weather are all cached, so
    # a tick mostly just re-scores. Skip DB writes on ticks.
    built = _build_predictions(persist=False)
    sliced = built["predictions"][:DEFAULT_LIMIT]
    return json.dumps(
        {
            "type": "predictions_update",
            "count": len(sliced),
            "total_matchups": built["total_matchups"],
            "data_source": built["data_source"],
            "batter_data_source": built["batter_data_source"],
            "weather_source": built["weather_source"],
            "predictions": sliced,
        }
    )


async def _live_update_loop():
    while True:
        try:
            if manager.active_connections:
                message = await asyncio.to_thread(_snapshot_message)
                await manager.broadcast(message)
        except Exception as e:  # pragma: no cover
            print(f"[websocket] live update loop error: {e}")
        await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)


def ensure_broadcast_loop_started():
    global _loop_started
    if not _loop_started:
        asyncio.create_task(_live_update_loop())
        _loop_started = True


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await manager.connect(websocket)
    ensure_broadcast_loop_started()

    try:
        # Immediate snapshot so the client isn't staring at an empty page for
        # up to 10 seconds. Run off-thread to avoid blocking the event loop.
        await websocket.send_text(await asyncio.to_thread(_snapshot_message))

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
