-- PuckLedger tables. Nothing here is readable with the public key: RLS is on with no policies,
-- and the server reads/writes only through these SECURITY DEFINER functions, which check a shared
-- secret stored as a SHA-256 hash in a schema the API doesn't expose. Idempotent.
CREATE SCHEMA IF NOT EXISTS pl_private;
REVOKE ALL ON SCHEMA pl_private FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS pl_private.secrets (name text PRIMARY KEY, sha256_hex text NOT NULL);
REVOKE ALL ON pl_private.secrets FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS public.pl_bets (
  bet_key text PRIMARY KEY,
  d date NOT NULL,
  game_id bigint,
  start_utc timestamptz,
  model text NOT NULL,
  kind text NOT NULL,
  market text,
  bet text,
  selection text,
  player_id bigint,
  team text,
  opp text,
  side text,
  line numeric,
  price int,
  book text,
  p_model numeric,
  ev numeric,
  note text,
  legs jsonb,
  source text NOT NULL DEFAULT 'live',
  posted_utc timestamptz,
  result text NOT NULL DEFAULT 'pending',
  units numeric,
  stat numeric,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pl_bets_d ON public.pl_bets (d);
ALTER TABLE public.pl_bets ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.pl_kv (k text PRIMARY KEY, v jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE public.pl_kv ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.pl_bets, public.pl_kv FROM anon, authenticated;

CREATE OR REPLACE FUNCTION pl_private.ok(secret text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pl_private, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM pl_private.secrets s
                 WHERE s.name = 'server' AND s.sha256_hex = encode(sha256(convert_to(secret, 'UTF8')), 'hex'))
$$;

CREATE OR REPLACE FUNCTION public.pl_put_bets(secret text, rows jsonb) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE n int;
BEGIN
  IF NOT pl_private.ok(secret) THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  INSERT INTO public.pl_bets AS b (bet_key, d, game_id, start_utc, model, kind, market, bet, selection, player_id, team, opp,
     side, line, price, book, p_model, ev, note, legs, source, posted_utc, result, units, stat, updated_at)
  SELECT r.bet_key, r.d, r.game_id, r.start_utc, r.model, r.kind, r.market, r.bet, r.selection, r.player_id, r.team, r.opp,
     r.side, r.line, r.price, r.book, r.p_model, r.ev, r.note, r.legs, coalesce(r.source, 'live'), r.posted_utc,
     coalesce(r.result, 'pending'), r.units, r.stat, now()
  FROM jsonb_populate_recordset(NULL::public.pl_bets, rows) r
  ON CONFLICT (bet_key) DO UPDATE SET result = excluded.result, units = excluded.units, stat = excluded.stat,
     legs = coalesce(excluded.legs, b.legs), updated_at = now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

CREATE OR REPLACE FUNCTION public.pl_get_bets(secret text, d_from date, d_to date) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT pl_private.ok(secret) THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  RETURN coalesce((SELECT jsonb_agg(to_jsonb(b) - 'updated_at' ORDER BY b.d, b.model, b.ev DESC)
                   FROM public.pl_bets b WHERE b.d BETWEEN d_from AND d_to), '[]'::jsonb);
END $$;

CREATE OR REPLACE FUNCTION public.pl_set_kv(secret text, key_name text, val jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT pl_private.ok(secret) THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  INSERT INTO public.pl_kv (k, v, updated_at) VALUES (key_name, val, now())
  ON CONFLICT (k) DO UPDATE SET v = excluded.v, updated_at = now();
END $$;

CREATE OR REPLACE FUNCTION public.pl_get_kv(secret text, keys text[]) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT pl_private.ok(secret) THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  RETURN coalesce((SELECT jsonb_object_agg(x.k, x.v) FROM public.pl_kv x WHERE x.k = ANY(keys)), '{}'::jsonb);
END $$;

CREATE OR REPLACE FUNCTION public.pl_kv_prefix(secret text, prefix text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT pl_private.ok(secret) THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  RETURN coalesce((SELECT jsonb_object_agg(x.k, x.v) FROM public.pl_kv x WHERE x.k LIKE prefix || '%'), '{}'::jsonb);
END $$;

REVOKE ALL ON FUNCTION pl_private.ok(text) FROM PUBLIC;
DO $$ DECLARE f text; BEGIN
  FOREACH f IN ARRAY ARRAY['pl_put_bets(text, jsonb)', 'pl_get_bets(text, date, date)', 'pl_set_kv(text, text, jsonb)',
                           'pl_get_kv(text, text[])', 'pl_kv_prefix(text, text)'] LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC', f);
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO anon, authenticated, service_role', f);
  END LOOP;
END $$;
