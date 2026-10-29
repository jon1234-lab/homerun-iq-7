-- NHL props model (anytime goals, 1+ points, shots on goal) — tables behind the HomerunIQ /nhl page.
-- Idempotent; safe to re-run. Written daily by the props pipeline (nhl/props/daily.py) using upserts.
-- Read-only for the public (anon / publishable key); writes only with the service role.

CREATE TABLE IF NOT EXISTS public.nhl_props_picks (
  pick_date date NOT NULL, market text NOT NULL, player_id bigint NOT NULL,
  rank int NOT NULL, player text NOT NULL, team text NOT NULL, opp text NOT NULL,
  p numeric(6,4) NOT NULL, fair_odds text, model text, result text NOT NULL DEFAULT 'Pending', stat int,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (pick_date, market, player_id));
CREATE INDEX IF NOT EXISTS nhl_props_picks_result_idx ON public.nhl_props_picks (result, pick_date DESC);

CREATE TABLE IF NOT EXISTS public.nhl_props_parlays (
  parlay_date date NOT NULL, type text NOT NULL, legs jsonb NOT NULL, legs_n int NOT NULL,
  corr numeric(6,3) NOT NULL DEFAULT 1, p numeric(6,4) NOT NULL, p_graded numeric(6,4), fair_odds text, model text,
  result text NOT NULL DEFAULT 'Pending', updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (parlay_date, type));

CREATE TABLE IF NOT EXISTS public.nhl_props_model_log (
  id bigserial PRIMARY KEY, log_date date NOT NULL, kind text NOT NULL, market text NOT NULL, idea text NOT NULL,
  champion text, champ_a numeric, chal_a numeric, champ_b numeric, chal_b numeric, result text,
  UNIQUE (log_date, kind, market, idea));

CREATE TABLE IF NOT EXISTS public.nhl_props_champion (
  market text PRIMARY KEY, config jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS public.nhl_props_snapshots (
  key text PRIMARY KEY, run_date date NOT NULL, data jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());

ALTER TABLE public.nhl_props_picks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nhl_props_parlays ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nhl_props_model_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nhl_props_champion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nhl_props_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS nhl_props_picks_read ON public.nhl_props_picks;
CREATE POLICY nhl_props_picks_read ON public.nhl_props_picks FOR SELECT USING (true);
DROP POLICY IF EXISTS nhl_props_parlays_read ON public.nhl_props_parlays;
CREATE POLICY nhl_props_parlays_read ON public.nhl_props_parlays FOR SELECT USING (true);
DROP POLICY IF EXISTS nhl_props_model_log_read ON public.nhl_props_model_log;
CREATE POLICY nhl_props_model_log_read ON public.nhl_props_model_log FOR SELECT USING (true);
DROP POLICY IF EXISTS nhl_props_champion_read ON public.nhl_props_champion;
CREATE POLICY nhl_props_champion_read ON public.nhl_props_champion FOR SELECT USING (true);
DROP POLICY IF EXISTS nhl_props_snapshots_read ON public.nhl_props_snapshots;
CREATE POLICY nhl_props_snapshots_read ON public.nhl_props_snapshots FOR SELECT USING (true);
