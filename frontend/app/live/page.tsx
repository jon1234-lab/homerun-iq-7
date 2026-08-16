"use client";

import { useEffect, useRef, useState } from "react";
import Leaderboard from "@/components/Leaderboard";
import StatusBadge from "@/components/StatusBadge";
import { combinedStatusBadge } from "@/lib/status";
import { AggregateSource, DataSource, Prediction } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/live";

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

export default function LivePage() {
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [totalMatchups, setTotalMatchups] = useState<number | null>(null);
  const [dataSource, setDataSource] = useState<DataSource | null>(null);
  const [batterSource, setBatterSource] = useState<AggregateSource | null>(null);
  const [weatherSource, setWeatherSource] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [lastTick, setLastTick] = useState<Date | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      setStatus("connecting");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!cancelled) setStatus("connected");
      };

      ws.onmessage = (event) => {
        if (cancelled) return;
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "predictions_update") {
            setPredictions(msg.predictions);
            setTotalMatchups(msg.total_matchups);
            setDataSource(msg.data_source);
            setBatterSource(msg.batter_data_source);
            setWeatherSource(msg.weather_source);
            setLastTick(new Date());
          }
        } catch {
          /* ignore malformed frames */
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        setStatus("disconnected");
        reconnectTimer.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        if (!cancelled) setStatus("error");
      };
    }

    connect();

    // Keep-alive so idle connections aren't dropped by proxies.
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send("ping");
    }, 20000);

    return () => {
      cancelled = true;
      clearInterval(pingInterval);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, []);

  const dotColor =
    status === "connected"
      ? "bg-emerald-500"
      : status === "connecting"
      ? "bg-yellow-500"
      : "bg-red-500";

  const badge = combinedStatusBadge(dataSource, batterSource, weatherSource);

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold sm:text-2xl">Live Feed</h1>
            <StatusBadge badge={badge} />
          </div>
          <p className="mt-1 text-xs text-gray-400 sm:text-sm">
            Streaming recalculations every 10 seconds.
            {totalMatchups !== null && totalMatchups > predictions.length && (
              <> Top {predictions.length} of {totalMatchups}.</>
            )}
            {lastTick && <> Last tick {lastTick.toLocaleTimeString()}.</>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs">
          <span className={`h-2 w-2 animate-pulse rounded-full ${dotColor}`} />
          <span className="capitalize text-gray-400">{status}</span>
        </div>
      </div>

      {status === "error" && predictions.length === 0 && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          Couldn&apos;t reach the live feed at <code>{WS_URL}</code>. Retrying automatically…
        </div>
      )}

      <Leaderboard
        predictions={predictions}
        emptyMessage={
          status === "connected" ? "Waiting for first update…" : "Connecting to live feed…"
        }
      />
    </div>
  );
}
