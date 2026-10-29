// NHL props data — read server-side from Supabase (public read-only tables, see nhl/props_migration.sql).

export const SUPABASE_URL = process.env.SUPABASE_URL || "https://lfpfdpfktvjpnjwmegyd.supabase.co";
const SUPABASE_KEY = process.env.SUPABASE_PUBLISHABLE_KEY || "sb_publishable_DyoU7kki74m2sYKvBqBHUQ_QsXc5BF9";

export type GoalRow = {
  date: string; player: string; playerId: number; team: string; opp: string; p_goal: number; fair_odds: string;
  toi: number; pp_toi: number; xg60: number; sog: number; finishing: number; due: number; opp_goalie: string;
};
export type PointRow = {
  date: string; player: string; playerId: number; team: string; opp: string; p_point: number; fair_odds: string;
  pts_pg: number; toi: number; pp_toi: number; opp_goalie: string;
};
export type ShotRow = {
  date: string; player: string; playerId: number; team: string; opp: string; sog_proj: number;
  p_sog2: number; p_sog3: number; p_sog4: number; fair2: string; fair3: string; fair4: string; sog: number; toi: number;
};
export type MarketRecord = { market: string; graded: number; hits: number; expected_hits: number; hit_rate: number | null; avg_p: number | null };
export type ParlayRecord = { parlay: string; graded: number; won: number; expected: number; win_rate: number };
export type ParlayBacktest = { parlay: string; nights: number; predicted_pct: number; actual_pct: number };
export type TopBacktest = { key: string; market: string; picks: string; predicted_pct: number; actual_pct: number; picks_n: number };
export type Summary = { run_date: string; data_through: string; champions?: Record<string, string>; notes?: string[] };
export type ParlayLeg = { player: string; playerId: number; team: string; market: string; p: number; result?: string };
export type Parlay = { parlay_date: string; type: string; legs: ParlayLeg[]; legs_n: number; p: number; fair_odds: string; model: string; result: string };
export type GameLog = { date: string; opp: string; toi: number; pp_toi: number; sog: number; xg: number; g: number; pts: number };
export type TeamPick = {
  team: string; opp: string; player: string; playerId: number; p_goal: number; fair_odds: string; opp_goalie: string;
  gp: number; g: number; pts: number; sog: number; xg: number; games: GameLog[];
};
export type Pick = { pick_date: string; market: string; rank: number; player: string; team: string; opp: string; p: number; fair_odds: string; model: string; result: string; stat: number | null };

async function sb<T>(path: string): Promise<T> {
  const headers: Record<string, string> = { apikey: SUPABASE_KEY };
  if (!SUPABASE_KEY.startsWith("sb_")) headers.Authorization = `Bearer ${SUPABASE_KEY}`;
  const res = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, { headers, cache: "no-store" });
  if (!res.ok) throw new Error(`NHL data request failed (${res.status})`);
  return res.json() as Promise<T>;
}

export type Snapshots = {
  today: GoalRow[]; today_pts: PointRow[]; today_sog: ShotRow[]; team_picks: TeamPick[]; msum: MarketRecord[]; parlay_summary: ParlayRecord[];
  parlay_bt: ParlayBacktest[]; top: TopBacktest[]; summary: Summary | null; updated_at: string | null;
};

export async function getSnapshots(): Promise<Snapshots> {
  const keys = ["today", "today_pts", "today_sog", "team_picks", "msum", "parlay_summary", "parlay_bt", "top", "summary"];
  const rows = await sb<{ key: string; data: unknown; updated_at: string }[]>(
    `nhl_props_snapshots?select=key,data,updated_at&key=in.(${keys.join(",")})`
  );
  const m = new Map(rows.map((r) => [r.key, r]));
  const get = <T,>(k: string, d: T): T => (m.get(k)?.data as T) ?? d;
  return {
    today: get("today", []), today_pts: get("today_pts", []), today_sog: get("today_sog", []), team_picks: get("team_picks", []), msum: get("msum", []),
    parlay_summary: get("parlay_summary", []), parlay_bt: get("parlay_bt", []), top: get("top", []),
    summary: get<Summary | null>("summary", null), updated_at: m.get("summary")?.updated_at ?? null,
  };
}

export async function getParlays(date: string): Promise<Parlay[]> {
  return sb<Parlay[]>(`nhl_props_parlays?select=parlay_date,type,legs,legs_n,p,fair_odds,model,result&parlay_date=eq.${date}&order=p.desc`);
}

export async function getRecentResults(limit = 90): Promise<Pick[]> {
  return sb<Pick[]>(
    `nhl_props_picks?select=pick_date,market,rank,player,team,opp,p,fair_odds,model,result,stat&result=in.(Hit,Miss)&order=pick_date.desc,market.asc,rank.asc&limit=${limit}`
  );
}

export async function getRecentParlays(limit = 30): Promise<Parlay[]> {
  return sb<Parlay[]>(
    `nhl_props_parlays?select=parlay_date,type,legs,legs_n,p,fair_odds,model,result&result=not.eq.Pending&order=parlay_date.desc,p.desc&limit=${limit}`
  );
}

export const pct = (p: number | null | undefined, d = 0) => (p == null ? "—" : `${(p * 100).toFixed(d)}%`);
