"use client";

import { useEffect, useState } from "react";
import { createCheckoutSession } from "@/lib/api";
import { Plan } from "@/lib/types";

const PLANS: { id: Plan; name: string; price: string; features: string[]; highlight?: boolean }[] = [
  {
    id: "free",
    name: "Free",
    price: "$0",
    features: ["Today's top matchups", "HRI scores & HR probability", "Live game status"],
  },
  {
    id: "pro",
    name: "Pro",
    price: "$19/mo",
    features: ["Full slate, every matchup", "Live WebSocket updates", "Trend indicators"],
    highlight: true,
  },
  {
    id: "elite",
    name: "Elite",
    price: "$49/mo",
    features: ["Everything in Pro", "HRI component breakdown", "Raw model inputs"],
  },
  {
    id: "edge",
    name: "Edge",
    price: "$99/mo",
    features: ["Everything in Elite", "Priority data refresh", "API access"],
  },
];

// Demo identity. In production this comes from your auth session.
const DEMO_USER_ID = "demo-user-001";

export default function UpgradePage() {
  const [loadingPlan, setLoadingPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("success")) setNotice("Payment complete — your plan is now active.");
    if (params.get("canceled")) setNotice("Checkout canceled. No charge was made.");
  }, []);

  async function handleUpgrade(plan: Plan) {
    if (plan === "free") return;
    setError(null);
    setLoadingPlan(plan);
    try {
      const { checkout_url } = await createCheckoutSession(plan, DEMO_USER_ID);
      window.location.href = checkout_url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start checkout.");
      setLoadingPlan(null);
    }
  }

  return (
    <div>
      <div className="mb-8 text-center">
        <h1 className="text-xl font-bold sm:text-2xl">Choose your plan</h1>
        <p className="mt-1 text-xs text-gray-400 sm:text-sm">
          Stripe test mode — pay with card 4242 4242 4242 4242.
        </p>
      </div>

      {notice && (
        <div className="mx-auto mb-6 max-w-2xl rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          {notice}
        </div>
      )}
      {error && (
        <div className="mx-auto mb-6 max-w-2xl rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {PLANS.map((plan) => (
          <div
            key={plan.id}
            className={`flex flex-col rounded-2xl border bg-white/[0.03] p-5 ${
              plan.highlight ? "border-emerald-400/40 ring-1 ring-emerald-400/20" : "border-white/10"
            }`}
          >
            <h2 className="font-bold">{plan.name}</h2>
            <p className="mb-4 mt-1 text-2xl font-extrabold">{plan.price}</p>
            <ul className="flex-1 space-y-2 text-sm text-gray-300">
              {plan.features.map((f) => (
                <li key={f} className="flex items-start gap-2">
                  <span className="mt-0.5 text-emerald-400">✓</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
            <button
              onClick={() => handleUpgrade(plan.id)}
              disabled={plan.id === "free" || loadingPlan !== null}
              className="mt-5 rounded-lg bg-emerald-500 py-2 font-semibold text-black transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {plan.id === "free"
                ? "Current plan"
                : loadingPlan === plan.id
                ? "Redirecting…"
                : "Choose plan"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
