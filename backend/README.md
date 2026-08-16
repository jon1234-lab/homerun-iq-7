# HomerunIQ Backend

FastAPI service powering real-time MLB home run predictions.

## Run locally

```bash
python3.11 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Status:   http://localhost:8000/

Runs with **zero configuration** — no Supabase, Redis, or Stripe keys needed
to boot. It falls back to in-memory storage and cache. Real MLB, Statcast, and
weather data all work without any API key.

## Layout

| Path | Purpose |
|---|---|
| `app/main.py` | Entrypoint, CORS, router registration |
| `app/config.py` | Environment-based settings |
| `app/database.py` | Supabase client + in-memory fallback |
| `app/services/mlb_data.py` | MLB Stats API: schedule, rosters, pitchers |
| `app/services/statcast.py` | Baseball Savant batter metrics via pybaseball |
| `app/services/weather.py` | Open-Meteo, signed wind vs. center field |
| `app/services/hri_engine.py` | The scoring model |
| `app/services/cache.py` | Redis with in-memory fallback |
| `app/services/plan_access.py` | Subscription plan gating |
| `app/routers/` | HTTP + WebSocket endpoints |
| `app/data/mock_data.py` | Fallback slate, park factors |

## Caching

First request each day is slow (fetches ~30 rosters plus the season Statcast
leaderboard). After that:

| Data | TTL |
|---|---|
| Schedule | 3 min |
| Rosters | 6 hr |
| Statcast leaderboard | 6 hr |
| Pitcher season stats | 1 hr |
| Weather | 15 min |

Set `REDIS_URL` to share the cache across multiple workers; otherwise each
process keeps its own in-memory copy.
