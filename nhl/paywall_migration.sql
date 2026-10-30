-- GoalIQ paywall: today's picks are private; graded history stays public.
-- Idempotent; safe to re-run. Applied 2026-10-30. The server's secret hash is inserted separately (never committed).
--
-- Public (publishable key) can read picks/parlays only once their date is "over"
-- (after 6am Eastern the next morning, so late West Coast games have finished).
-- The GoalIQ server reads today's data through goaliq_read(), which checks a shared secret
-- stored only as a SHA-256 hash in a schema the API doesn't expose.

CREATE SCHEMA IF NOT EXISTS goaliq_private;
REVOKE ALL ON SCHEMA goaliq_private FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS goaliq_private.secrets (name text PRIMARY KEY, sha256_hex text NOT NULL);
REVOKE ALL ON goaliq_private.secrets FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.goaliq_public_cutoff() RETURNS date
LANGUAGE sql STABLE AS $$ SELECT ((now() AT TIME ZONE 'America/New_York') - interval '6 hours')::date $$;

-- Restrictive policies are ANDed with the existing public-read policies, so nothing is dropped.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'goaliq_paywall_picks') THEN
    CREATE POLICY goaliq_paywall_picks ON public.nhl_props_picks AS RESTRICTIVE FOR SELECT TO anon, authenticated
      USING (pick_date < public.goaliq_public_cutoff());
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'goaliq_paywall_parlays') THEN
    CREATE POLICY goaliq_paywall_parlays ON public.nhl_props_parlays AS RESTRICTIVE FOR SELECT TO anon, authenticated
      USING (parlay_date < public.goaliq_public_cutoff());
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'goaliq_paywall_snapshots') THEN
    CREATE POLICY goaliq_paywall_snapshots ON public.nhl_props_snapshots AS RESTRICTIVE FOR SELECT TO anon, authenticated
      USING (key NOT IN ('today', 'today_pts', 'today_sog', 'team_picks') AND key NOT LIKE 'board%');
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.goaliq_read(secret text, what text, d date DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE ok boolean;
BEGIN
  SELECT EXISTS (SELECT 1 FROM goaliq_private.secrets s
                 WHERE s.name = 'server' AND s.sha256_hex = encode(sha256(convert_to(secret, 'UTF8')), 'hex')) INTO ok;
  IF NOT ok THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  IF what = 'picks' THEN
    RETURN coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.market, x.rank) FROM public.nhl_props_picks x
                     WHERE x.pick_date = d AND x.result <> 'Replaced'), '[]'::jsonb);
  ELSIF what = 'parlays' THEN
    RETURN coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.type) FROM public.nhl_props_parlays x
                     WHERE x.parlay_date = d), '[]'::jsonb);
  ELSIF what = 'snapshots' THEN
    RETURN coalesce((SELECT jsonb_object_agg(s.key, jsonb_build_object('run_date', s.run_date, 'data', s.data))
                     FROM public.nhl_props_snapshots s), '{}'::jsonb);
  ELSIF what = 'record' THEN
    RETURN jsonb_build_object(
      'picks', coalesce((SELECT jsonb_agg(jsonb_build_object('d', p.pick_date, 'm', p.market, 'r', p.rank, 'p', p.p, 'res', p.result))
                         FROM public.nhl_props_picks p WHERE p.result IN ('Hit', 'Miss')), '[]'::jsonb),
      'parlays', coalesce((SELECT jsonb_agg(jsonb_build_object('d', p.parlay_date, 't', p.type, 'p', coalesce(p.p_graded, p.p), 'res', p.result))
                           FROM public.nhl_props_parlays p WHERE p.result LIKE 'Won%' OR p.result LIKE 'Lost%'), '[]'::jsonb));
  END IF;
  RAISE EXCEPTION 'unknown request %', what;
END $$;
REVOKE ALL ON FUNCTION public.goaliq_read(text, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.goaliq_read(text, text, date) TO anon, authenticated, service_role;
