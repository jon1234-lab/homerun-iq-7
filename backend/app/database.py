"""
Database access layer.

Uses Supabase (Postgres) when SUPABASE_URL / SUPABASE_KEY are configured.
If they are not, falls back to a simple in-memory store so the app is fully
runnable out of the box with zero external services.

Swapping in real Supabase credentials in .env upgrades persistence to real
Postgres with no code changes required by any caller.
"""
from typing import Optional
from app.config import settings

_supabase_client = None
_supabase_enabled = bool(settings.SUPABASE_URL and settings.SUPABASE_KEY)

if _supabase_enabled:
    try:
        from supabase import create_client

        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        print("[database] Supabase connected.")
    except Exception as exc:  # pragma: no cover
        print(f"[database] Supabase init failed, using in-memory store: {exc}")
        _supabase_enabled = False
else:
    print("[database] No Supabase credentials set - using in-memory store.")


class InMemoryStore:
    def __init__(self):
        self.users: dict[str, dict] = {}
        self.subscriptions: dict[str, dict] = {}
        self.predictions: dict[str, dict] = {}
        self.game_state: dict[str, dict] = {}

    def upsert_user(self, user: dict) -> dict:
        self.users[user["id"]] = {**self.users.get(user["id"], {}), **user}
        return self.users[user["id"]]

    def get_user(self, user_id: str) -> Optional[dict]:
        return self.users.get(user_id)

    def upsert_subscription(self, sub: dict) -> dict:
        self.subscriptions[sub["user_id"]] = {**self.subscriptions.get(sub["user_id"], {}), **sub}
        return self.subscriptions[sub["user_id"]]

    def get_subscription(self, user_id: str) -> Optional[dict]:
        return self.subscriptions.get(user_id)

    def save_prediction(self, prediction: dict) -> dict:
        self.predictions[prediction["player_id"]] = prediction
        return prediction

    def all_predictions(self) -> list[dict]:
        return list(self.predictions.values())

    def save_game_state(self, state: dict) -> dict:
        self.game_state[state["game_id"]] = state
        return state

    def all_game_state(self) -> list[dict]:
        return list(self.game_state.values())


_memory_store = InMemoryStore()

# Columns that actually exist in the predictions / game_state tables.
# Anything else (e.g. transient UI-only fields) is stripped before writing,
# so adding a new API field never breaks DB inserts.
_PREDICTION_COLUMNS = {
    "player_id", "player_name", "team", "game_id", "opponent_pitcher",
    "hri_score", "hr_probability", "trend", "park", "wind",
    "temperature", "park_factor",
}
_GAME_STATE_COLUMNS = {
    "game_id", "home_team", "away_team", "park", "park_factor",
    "starting_pitcher_home", "starting_pitcher_away", "status",
}


def is_supabase_enabled() -> bool:
    return _supabase_enabled


def get_supabase():
    return _supabase_client


def get_memory_store() -> InMemoryStore:
    return _memory_store


def _filtered(record: dict, allowed: set) -> dict:
    return {k: v for k, v in record.items() if k in allowed}


def upsert_user(user: dict) -> dict:
    if _supabase_enabled:
        try:
            res = _supabase_client.table("users").upsert(user).execute()
            return res.data[0] if res.data else user
        except Exception as e:
            print(f"[database] upsert_user failed: {e}")
            return user
    return _memory_store.upsert_user(user)


def get_user(user_id: str) -> Optional[dict]:
    if _supabase_enabled:
        try:
            res = _supabase_client.table("users").select("*").eq("id", user_id).limit(1).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"[database] get_user failed: {e}")
            return None
    return _memory_store.get_user(user_id)


def upsert_subscription(sub: dict) -> dict:
    if _supabase_enabled:
        try:
            res = _supabase_client.table("subscriptions").upsert(sub, on_conflict="user_id").execute()
            return res.data[0] if res.data else sub
        except Exception as e:
            print(f"[database] upsert_subscription failed: {e}")
            return sub
    return _memory_store.upsert_subscription(sub)


def get_subscription(user_id: str) -> Optional[dict]:
    if _supabase_enabled:
        try:
            res = (
                _supabase_client.table("subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"[database] get_subscription failed: {e}")
            return None
    return _memory_store.get_subscription(user_id)


def save_prediction(prediction: dict) -> dict:
    if _supabase_enabled:
        try:
            _supabase_client.table("predictions").insert(_filtered(prediction, _PREDICTION_COLUMNS)).execute()
        except Exception as e:
            print(f"[database] save_prediction failed: {e}")
        return prediction
    return _memory_store.save_prediction(prediction)


def all_predictions() -> list[dict]:
    if _supabase_enabled:
        try:
            res = _supabase_client.table("predictions").select("*").execute()
            return res.data or []
        except Exception as e:
            print(f"[database] all_predictions failed: {e}")
            return []
    return _memory_store.all_predictions()


def save_game_state(state: dict) -> dict:
    if _supabase_enabled:
        try:
            _supabase_client.table("game_state").upsert(
                _filtered(state, _GAME_STATE_COLUMNS), on_conflict="game_id"
            ).execute()
        except Exception as e:
            print(f"[database] save_game_state failed: {e}")
        return state
    return _memory_store.save_game_state(state)


def all_game_state() -> list[dict]:
    if _supabase_enabled:
        try:
            res = _supabase_client.table("game_state").select("*").execute()
            return res.data or []
        except Exception as e:
            print(f"[database] all_game_state failed: {e}")
            return []
    return _memory_store.all_game_state()
