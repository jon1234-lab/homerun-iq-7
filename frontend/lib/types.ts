export type BatterStatsSource = "real_statcast" | "league_average_placeholder" | "mock";
export type DataSource = "live" | "mock";
export type AggregateSource = "live" | "mixed" | "mock";

export interface Prediction {
  player_id: string;
  player_name: string;
  team: string;
  game_id: string;
  game_status: string;
  opponent_pitcher: string;
  hri_score: number;
  hr_probability: number;
  trend: number;
  park: string;
  wind: number;
  temperature: number;
  park_factor: number;
  batter_stats_source: BatterStatsSource;
  pitcher_stats_source: string;
  weather_source: string;
}

export interface Game {
  game_id: string;
  home_team: string;
  away_team: string;
  park: string;
  park_factor: number;
  starting_pitcher_home: string;
  starting_pitcher_away: string;
  game_time: string | null;
  status: string;
  data_source: DataSource;
}

export interface PlayerDetail {
  id: string;
  name: string;
  team: string;
  team_name: string;
  park: string;
  park_factor: number;
  barrel_rate: number;
  hard_hit_rate: number;
  xslg: number;
  exit_velocity: number;
  stats_source: string;
}

export type Plan = "free" | "pro" | "elite" | "edge";

export interface PredictionsResponse {
  count: number;
  total_matchups: number;
  data_source: DataSource;
  batter_data_source: AggregateSource;
  weather_source: string;
  predictions: Prediction[];
}

export interface GamesResponse {
  count: number;
  data_source: DataSource;
  games: Game[];
}
