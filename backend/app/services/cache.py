"""
Simple cache layer. Uses Redis if reachable, otherwise falls back to an
in-process dict so the app runs with zero extra infrastructure.
"""
import json
import time
from typing import Any, Optional
from app.config import settings

_redis_client = None
_redis_enabled = False

if settings.REDIS_URL:
    try:
        import redis

        _redis_client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.5)
        _redis_client.ping()
        _redis_enabled = True
        print("[cache] Redis connected.")
    except Exception as e:
        print(f"[cache] Redis unavailable, using in-memory cache: {e}")
        _redis_enabled = False
        _redis_client = None

_local_cache: dict[str, tuple[float, Any]] = {}


def set_cache(key: str, value: Any, ttl_seconds: int = 30) -> None:
    if _redis_enabled:
        try:
            _redis_client.setex(key, ttl_seconds, json.dumps(value))
            return
        except Exception:
            pass
    _local_cache[key] = (time.time() + ttl_seconds, value)


def get_cache(key: str) -> Optional[Any]:
    if _redis_enabled:
        try:
            raw = _redis_client.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            pass
    entry = _local_cache.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if time.time() > expires_at:
        _local_cache.pop(key, None)
        return None
    return value


def cache_enabled_backend() -> str:
    return "redis" if _redis_enabled else "memory"
