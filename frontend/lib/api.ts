import { GamesResponse, PredictionsResponse, PlayerDetail } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail || "";
    } catch {
      detail = await res.text().catch(() => "");
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function fetchTodaysPredictions(limit = 50): Promise<PredictionsResponse> {
  const res = await fetch(`${API_URL}/api/predictions/today?limit=${limit}`, { cache: "no-store" });
  return handleResponse<PredictionsResponse>(res);
}

export async function fetchTodaysGames(): Promise<GamesResponse> {
  const res = await fetch(`${API_URL}/api/games/today`, { cache: "no-store" });
  return handleResponse<GamesResponse>(res);
}

export async function fetchPlayer(playerId: string): Promise<PlayerDetail> {
  const res = await fetch(`${API_URL}/api/player/${playerId}`, { cache: "no-store" });
  return handleResponse<PlayerDetail>(res);
}

export async function createCheckoutSession(
  plan: "pro" | "elite" | "edge",
  userId: string,
  email?: string
): Promise<{ checkout_url: string; session_id: string }> {
  const res = await fetch(`${API_URL}/api/stripe/create-checkout-session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan, user_id: userId, email }),
  });
  return handleResponse(res);
}

export { API_URL };
