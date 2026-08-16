"use client";

import { useEffect, useState } from "react";
import { fetchTodaysGames } from "@/lib/api";
import { Game } from "@/lib/types";

function formatTime(iso: string | null) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch {
    return "";
  }
}

function parkFactorLabel(pf: number) {
  if (pf >= 1.1) return { text: "HR-friendly", cls: "text-emerald-400" };
  if (pf <= 0.92) return { text: "Pitcher-friendly", cls: "text-blue-400" };
  return { text: "Neutral", cls: "text-gray-400" };
}

export default function GamesPage() {
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchTodaysGames();
        if (!cancelled) setGames(data.games);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load games");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <h1 className="mb-1 text-xl font-bold sm:text-2xl">Today&apos;s Games</h1>
      <p className="mb-5 text-xs text-gray-400 sm:text-sm">
        {games.length > 0 ? `${games.length} games on the slate.` : "Live MLB schedule."}
      </p>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-16 text-center text-sm text-gray-500">Loading schedule…</div>
      ) : games.length === 0 ? (
        <div className="py-16 text-center text-sm text-gray-500">No games scheduled today.</div>
      ) : (
        <div className="flex flex-col gap-2">
          {games.map((g) => {
            const pf = parkFactorLabel(g.park_factor);
            return (
              <div
                key={g.game_id}
                className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-white">
                      {g.away_team} @ {g.home_team}
                    </p>
                    <p className="truncate text-xs text-gray-400">
                      {g.starting_pitcher_away} vs {g.starting_pitcher_home}
                    </p>
                    <p className="truncate text-[11px] text-gray-600">
                      {g.park} ·{" "}
                      <span className={pf.cls}>
                        {pf.text} ({g.park_factor.toFixed(2)})
                      </span>
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-xs font-medium text-gray-300">{g.status}</p>
                    <p className="text-[11px] text-gray-600">{formatTime(g.game_time)}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
