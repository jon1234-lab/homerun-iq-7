# GoalIQ — NHL anytime goalscorer model

Self-contained in this folder, alongside HomerunIQ. Render web service `goal-iq` (start: `cd nhl && uvicorn main:app --host 0.0.0.0 --port $PORT`) and cron `goal-iq-cron` (`cd nhl && python jobs.py`).

- `goal_engine.py` Poisson goal model; `nhl_data.py` NHL.com + DailyFaceoff fetchers (no keys); `slate.py` builds a night (`python slate.py 2026-10-13`)
- `main.py` FastAPI: /api/slate, /api/live, /api/track-record, page at /
- `jobs.py` cron: save pregame predictions, lock at puck drop, grade FINAL games
- `db.py` + `migration.sql`: Supabase tables nhl_predictions, nhl_games. Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Backtest 2025-26 (10,115 player-games, pre-game data only): log loss 0.398 vs 0.419 naive vs 0.428 base rate.
