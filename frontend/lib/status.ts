import { AggregateSource, DataSource } from "./types";

export interface StatusBadge {
  label: string;
  colorClass: string;
  detail: string;
}

export function combinedStatusBadge(
  gameSource: DataSource | null,
  batterSource: AggregateSource | null,
  weatherSource?: string | null
): StatusBadge | null {
  if (!gameSource) return null;

  const liveWeather = weatherSource === "live";

  if (gameSource === "live" && batterSource === "live") {
    return {
      label: "● FULLY LIVE",
      colorClass: "bg-emerald-500/20 text-emerald-400 ring-1 ring-emerald-400/30",
      detail: `Real MLB games, real Statcast stats${liveWeather ? ", real weather" : ""}.`,
    };
  }
  if (gameSource === "live" && batterSource === "mixed") {
    return {
      label: "● LIVE",
      colorClass: "bg-emerald-500/20 text-emerald-400 ring-1 ring-emerald-400/30",
      detail: `Real MLB games and Statcast stats${liveWeather ? ", real weather" : ""}. A few players without tracked batted balls use league-average placeholders.`,
    };
  }
  if (gameSource === "live") {
    return {
      label: "LIVE GAMES · EST. STATS",
      colorClass: "bg-blue-500/20 text-blue-400 ring-1 ring-blue-400/30",
      detail: "Real MLB games, but Statcast batter stats were unavailable.",
    };
  }
  return {
    label: "DEMO DATA",
    colorClass: "bg-amber-500/20 text-amber-400 ring-1 ring-amber-400/30",
    detail: "No live MLB games available right now, so a sample slate is shown.",
  };
}

export function isEstimatedStats(source: string): boolean {
  return source !== "real_statcast";
}
