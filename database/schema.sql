-- ============================================================
-- HomerunIQ Database Schema (Supabase / Postgres)
-- Run in the Supabase SQL editor, or via psql.
-- Safe to re-run: everything is IF NOT EXISTS / idempotent.
-- ============================================================

create extension if not exists "pgcrypto";

-- ------------------------------------------------------------
-- users
-- ------------------------------------------------------------
create table if not exists users (
    id text primary key,
    email text unique,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- subscriptions
-- ------------------------------------------------------------
create table if not exists subscriptions (
    id uuid primary key default gen_random_uuid(),
    user_id text not null references users(id) on delete cascade,
    plan text not null default 'free' check (plan in ('free','pro','elite','edge')),
    status text not null default 'active'
        check (status in ('active','canceled','past_due','incomplete','unpaid')),
    stripe_customer_id text,
    stripe_subscription_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id)
);

-- ------------------------------------------------------------
-- predictions
-- ------------------------------------------------------------
create table if not exists predictions (
    id uuid primary key default gen_random_uuid(),
    player_id text not null,
    player_name text not null,
    team text not null,
    game_id text not null,
    opponent_pitcher text,
    hri_score numeric(5,2) not null,
    hr_probability numeric(5,3) not null,
    trend numeric(5,2) not null default 0,
    park text,
    wind numeric(5,2),
    temperature numeric(5,2),
    park_factor numeric(4,3),
    created_at timestamptz not null default now()
);

create index if not exists idx_predictions_player_id on predictions(player_id);
create index if not exists idx_predictions_game_id on predictions(game_id);
create index if not exists idx_predictions_created_at on predictions(created_at desc);

-- One row per player per game: the backend upserts on this pair, so a
-- retried request or repeated polling updates the existing row instead of
-- inserting a duplicate.
create unique index if not exists idx_predictions_player_game
  on predictions(player_id, game_id);

-- ------------------------------------------------------------
-- game_state
-- ------------------------------------------------------------
create table if not exists game_state (
    game_id text primary key,
    home_team text not null,
    away_team text not null,
    park text,
    park_factor numeric(4,3),
    starting_pitcher_home text,
    starting_pitcher_away text,
    status text not null default 'scheduled',
    updated_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- updated_at auto-touch
-- ------------------------------------------------------------
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_users_updated_at on users;
create trigger trg_users_updated_at before update on users
  for each row execute function set_updated_at();

drop trigger if exists trg_subscriptions_updated_at on subscriptions;
create trigger trg_subscriptions_updated_at before update on subscriptions
  for each row execute function set_updated_at();

drop trigger if exists trg_game_state_updated_at on game_state;
create trigger trg_game_state_updated_at before update on game_state
  for each row execute function set_updated_at();

-- ------------------------------------------------------------
-- Row Level Security
-- The backend connects with the service_role key, which bypasses RLS.
-- These policies allow public READ of leaderboard data only; users and
-- subscriptions stay service-role-only.
-- ------------------------------------------------------------
alter table users enable row level security;
alter table subscriptions enable row level security;
alter table predictions enable row level security;
alter table game_state enable row level security;

drop policy if exists "Public read access to predictions" on predictions;
create policy "Public read access to predictions"
  on predictions for select using (true);

drop policy if exists "Public read access to game_state" on game_state;
create policy "Public read access to game_state"
  on game_state for select using (true);
