export default function TrendIndicator({ trend }: { trend: number }) {
  if (trend > 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-emerald-400 text-xs font-semibold tabular-nums">
        ▲{trend.toFixed(1)}
      </span>
    );
  }
  if (trend < 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-red-400 text-xs font-semibold tabular-nums">
        ▼{Math.abs(trend).toFixed(1)}
      </span>
    );
  }
  return <span className="text-xs text-gray-600 tabular-nums">—</span>;
}
