import { Prediction } from "@/lib/types";
import { isEstimatedStats } from "@/lib/status";
import TrendIndicator from "./TrendIndicator";

function scoreStyles(score: number) {
  if (score >= 55) return "text-emerald-400 border-emerald-400/50 bg-emerald-400/5";
  if (score >= 40) return "text-yellow-400 border-yellow-400/40 bg-yellow-400/5";
  return "text-gray-400 border-gray-600/40";
}

function statusStyles(status: string) {
  const s = status.toLowerCase();
  if (s.includes("progress")) return "text-emerald-400";
  if (s.includes("final")) return "text-gray-500";
  return "text-gray-400";
}

export default function PlayerCard({
  prediction,
  rank,
}: {
  prediction: Prediction;
  rank: number;
}) {
  const estimated = isEstimatedStats(prediction.batter_stats_source);
  const windLabel =
    prediction.wind > 1
      ? `${prediction.wind.toFixed(0)} mph out`
      : prediction.wind < -1
      ? `${Math.abs(prediction.wind).toFixed(0)} mph in`
      : "calm";

  return (
    <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-3 transition-colors hover:bg-white/[0.07] sm:px-4">
      <span className="w-6 shrink-0 text-right font-mono text-sm text-gray-600">{rank}</span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="truncate font-semibold text-white">{prediction.player_name}</p>
          {estimated && (
            <span
              title="Statcast data unavailable for this player — league-average estimate used"
              className="shrink-0 rounded border border-gray-600 px-1 text-[9px] uppercase leading-tight text-gray-500"
            >
              est
            </span>
          )}
        </div>
        <p className="truncate text-xs text-gray-400">
          {prediction.team} vs {prediction.opponent_pitcher}
        </p>
        <p className="truncate text-[11px] text-gray-600">
          {prediction.park} · {prediction.temperature.toFixed(0)}°F · {windLabel} ·{" "}
          <span className={statusStyles(prediction.game_status)}>{prediction.game_status}</span>
        </p>
      </div>

      <div className="shrink-0 text-right">
        <p className="font-mono text-sm text-white tabular-nums">
          {(prediction.hr_probability * 100).toFixed(1)}%
        </p>
        <p className="text-[10px] uppercase tracking-wide text-gray-600">HR prob</p>
        <TrendIndicator trend={prediction.trend} />
      </div>

      <div
        className={`flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-full border-2 font-bold ${scoreStyles(
          prediction.hri_score
        )}`}
      >
        <span className="text-base leading-none tabular-nums">
          {prediction.hri_score.toFixed(0)}
        </span>
        <span className="text-[8px] uppercase tracking-wide text-gray-500">HRI</span>
      </div>
    </div>
  );
}
