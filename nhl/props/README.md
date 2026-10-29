# NHL props model — anytime goals, 1+ points, shots on goal

Powers the `/nhl` page of the HomerunIQ frontend. Runs once each morning (scheduled task).

- `daily.py` — downloads MoneyPuck game-by-game + shot data, grades the previous night's logged picks and parlays from NHL.com box scores, tests new model ideas per market against the current model on held-out games (an idea is adopted only if it wins on both test windows), projects today's slate, logs the top 10 picks per market and the nightly parlays, and writes `out/supabase_state.sql` + `out/supabase_snapshots.sql`.
- `build_xlsx.py` — builds the daily tracker workbook from `out/`.
- `../props_migration.sql` — the `nhl_props_*` Supabase tables (public read-only; written with upserts, no deletes).

```
python3 daily.py --run-date 2026-10-30 --out ./out --work ./work --goalies goalies.json \
  --supabase-key sb_publishable_...   # reads saved picks/model state from Supabase
```

Models (Poisson regression; shots use a negative binomial): goal `v2.x`, point `p1.x`, shots `s1.x`.
Backtest 2025-26 (trained on 2024-25 only): predicted vs actual hit rates match within ~1–3 points at every probability level.
