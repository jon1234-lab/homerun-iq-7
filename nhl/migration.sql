-- GoalIQ tables (idempotent; safe to re-run)
CREATE TABLE IF NOT EXISTS public.nhl_predictions (
  game_date date NOT NULL, game_id bigint NOT NULL, player_id bigint NOT NULL,
  name text NOT NULL, team text NOT NULL, opp text NOT NULL, home boolean NOT NULL,
  pos text, line text, pp_unit text, gtd boolean DEFAULT false,
  p_goal numeric(6,4) NOT NULL, fair_odds integer, exp_shots numeric(5,2), sh_pct_reg numeric(6,4),
  proj_ev_min numeric(4,1), proj_pp_min numeric(4,1), goalie_mult numeric(5,3), defense_mult numeric(5,3),
  opp_goalie text, gp_sample numeric(6,1), model_version text NOT NULL DEFAULT 'goaliq-v1',
  locked boolean NOT NULL DEFAULT false, scored boolean, goals integer, graded_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (game_id, player_id));
CREATE INDEX IF NOT EXISTS nhl_predictions_date_idx ON public.nhl_predictions (game_date);
CREATE INDEX IF NOT EXISTS nhl_predictions_ungraded_idx ON public.nhl_predictions (game_id) WHERE scored IS NULL;
CREATE TABLE IF NOT EXISTS public.nhl_games (
  game_id bigint PRIMARY KEY, game_date date NOT NULL, away text NOT NULL, home text NOT NULL,
  start_utc timestamptz, state text, away_goalie text, home_goalie text,
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS nhl_games_date_idx ON public.nhl_games (game_date);
ALTER TABLE public.nhl_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nhl_games ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS nhl_predictions_read ON public.nhl_predictions;
CREATE POLICY nhl_predictions_read ON public.nhl_predictions FOR SELECT USING (true);
DROP POLICY IF EXISTS nhl_games_read ON public.nhl_games;
CREATE POLICY nhl_games_read ON public.nhl_games FOR SELECT USING (true);
