# PuckLedger — NHL betting models, graded in public

Standalone site (Render web service, root `puckledger/`). Start: `cd puckledger/web && uvicorn main:app --host 0.0.0.0 --port $PORT`.

- `engine/` — data (NHL stats API, DailyFaceoff), features (walk-forward), models (Poisson / neg. binomial, team Poisson with
  empty-net correction), odds (The Odds API), betting-model rules + grading, research tools, and the background loop (`run.py`).
- `data/` — shipped history (2021-26 game logs, career vs-team splits since 2015-16) and fitted coefficients (`coef.json`).
- `web/` — FastAPI app + single-page front end. Whop sign-in (`auth.py`) gates tonight's bets and model probabilities.
- `migration.sql` — Supabase tables `pl_bets`, `pl_kv` (no public access) and the `pl_*` functions the server uses.

Env: PL_DB_SECRET, ODDS_API_KEY, WHOP_CLIENT_ID, WHOP_API_KEY, WHOP_PRODUCT_ID, WHOP_CHECKOUT_URL, PUBLIC_URL,
SESSION_SECRET, PREVIEW_KEY, PRICE_LABEL.

Loop (every 10 min): refresh this season's games, grade finished bets, post bets ~3.5h before each game at the best price
among DK/FD/MGM/Caesars, replay past 2026-27 nights with historical odds once (labelled 'backfill'), update records.
