"use client";

import { useState } from "react";
import { StatusBadge as Badge } from "@/lib/status";

export default function StatusBadge({ badge }: { badge: Badge | null }) {
  const [open, setOpen] = useState(false);
  if (!badge) return null;

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide ${badge.colorClass}`}
      >
        {badge.label}
      </button>
      {open && (
        <span className="absolute left-0 top-full z-20 mt-1.5 w-64 rounded-lg border border-white/10 bg-diamond-900 px-3 py-2 text-[11px] font-normal leading-relaxed text-gray-300 shadow-xl">
          {badge.detail}
        </span>
      )}
    </span>
  );
}
