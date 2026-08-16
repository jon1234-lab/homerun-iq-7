"use client";

import { useCallback, useEffect, useState } from "react";
import Leaderboard from "@/components/Leaderboard";
import StatusBadge from "@/components/StatusBadge";
import { fetchTodaysPredictions } from "@/lib/api";
import { combinedStatusBadge } from "@/lib/status";
import { AggregateSource, DataSource, Prediction } from "@/lib/types";

export default function DashboardPage() {
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [totalMatchups, setTotalMatchups] = useState<number | null>(null);
  const [dataSource, setDataSource] = useState<DataSource | null>(null);
  const [batterSource, setBatterSource] = useState<AggregateSource | null>(null);
  const [weatherSource, setWeatherSource] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(async (isManual = false) => {
    if (isManual) setRefreshing(true);
    try {
      setError(null);
      const data = await fetchTodaysPredictions();
      setPredictions(data.predictions);
      setTotalMatchups(data.total_matchups);
      setDataSource(data.data_source);
      setBatterSource(data.batter_data_source);
      setWeatherSource(data.weather_source);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load predictions");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(() => load(), 30000);
    return () => clearInterval(interval);
  }, [load]);

  const badge = combinedStatusBadge(dataSource, batterSource, weatherSource);

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold sm:text-2xl">Today&apos;s HR Board</h1>
            <StatusBadge badge={badge} />
          </div>
          <p className="mt-1 text-xs text-gray-400 sm:text-sm">
            Ranked by Home Run Intelligence score.
            {totalMatchups !== null && totalMatchups > predictions.length && (
              <> Top {predictions.length} of {totalMatchups} matchups.</>
            )}
            {lastUpdated && <> Updated {lastUpdated.toLocaleTimeString()}.</>}
          </p>
        </div>
        <button
          onClick={() => load(true)}
          disabled={refreshing}
          className="shrink-0 rounded-lg border border-white/20 px-3 py-2 text-sm font-medium transition-colors hover:bg-white/10 disabled:opacity-50"
        >
          {refreshing ? "…" : "↻"}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
          <div className="mt-1 text-xs text-red-400/80">
            Check that the backend is reachable at{" "}
            <code>{process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}</code>.
          </div>
        </div>
      )}

      {loading ? (
        <div className="py-16 text-center text-sm text-gray-500">
          Loading today&apos;s slate…
          <div className="mt-1 text-xs text-gray-600">
            First load pulls live rosters and Statcast data — this can take a few seconds.
          </div>
        </div>
      ) : (
        <Leaderboard
          predictions={predictions}
          emptyMessage="No matchups available right now. There may be no games scheduled today."
        />
      )}
    </div>
  );
}
