import { Prediction } from "@/lib/types";
import PlayerCard from "./PlayerCard";

export default function Leaderboard({
  predictions,
  emptyMessage = "No predictions available yet.",
}: {
  predictions: Prediction[];
  emptyMessage?: string;
}) {
  if (predictions.length === 0) {
    return <div className="py-12 text-center text-sm text-gray-500">{emptyMessage}</div>;
  }

  return (
    <div className="flex flex-col gap-2">
      {predictions.map((p, i) => (
        <PlayerCard key={`${p.player_id}-${p.game_id}`} prediction={p} rank={i + 1} />
      ))}
    </div>
  );
}
