import type { Metadata } from "next";
import Tabs from "@/components/nhl/Tabs";
import {
  GoalRow, Parlay, Pick, PointRow, ShotRow, Snapshots, TeamPick,
  getParlays, getRecentParlays, getRecentResults, getSnapshots, pct,
} from "@/lib/nhl";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "NHL Props Board — HomerunIQ",
  description: "Model probabilities for NHL anytime goals, points and shots on goal, with fair odds, parlays and a graded track record.",
};

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-white/10 bg-white/[0.03] ${className}`}>{children}</div>;
}

function Row({ rank, name, sub, meta, big, label, fair }: {
  rank: number; name: string; sub: string; meta: string; big: string; label: string; fair: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-3 transition-colors hover:bg-white/[0.07] sm:px-4">
      <span className="w-6 shrink-0 text-right font-mono text-sm text-gray-600">{rank}</span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-semibold text-white">{name}</p>
        <p className="truncate text-xs text-gray-400">{sub}</p>
        <p className="truncate text-[11px] text-gray-600">{meta}</p>
      </div>
      <div className="shrink-0 text-right">
        <p className="font-mono text-base font-semibold text-emerald-400 tabular-nums">{big}</p>
        <p className="text-[10px] uppercase tracking-wide text-gray-600">{label}</p>
        <p className="font-mono text-[11px] text-gray-400 tabular-nums">Fair {fair}</p>
      </div>
    </div>
  );
}

const Empty = ({ text }: { text: string }) => <div className="py-12 text-center text-sm text-gray-500">{text}</div>;

function Goals({ rows }: { rows: GoalRow[] }) {
  if (!rows.length) return <Empty text="No games today." />;
  return (
    <div className="flex flex-col gap-2">
      {rows.map((r, i) => (
        <Row key={r.playerId} rank={i + 1} name={r.player} sub={`${r.team} ${r.opp} · vs ${r.opp_goalie}`}
          meta={`${r.toi.toFixed(1)} TOI · ${r.pp_toi.toFixed(1)} PP · ${r.xg60.toFixed(2)} xG/60 · ${r.sog.toFixed(1)} SOG/gm`}
          big={pct(r.p_goal)} label="Anytime goal" fair={r.fair_odds} />
      ))}
    </div>
  );
}

function Points({ rows }: { rows: PointRow[] }) {
  if (!rows.length) return <Empty text="No games today." />;
  return (
    <div className="flex flex-col gap-2">
      {rows.map((r, i) => (
        <Row key={r.playerId} rank={i + 1} name={r.player} sub={`${r.team} ${r.opp} · vs ${r.opp_goalie}`}
          meta={`${r.pts_pg.toFixed(2)} pts/gm · ${r.toi.toFixed(1)} TOI · ${r.pp_toi.toFixed(1)} PP`}
          big={pct(r.p_point)} label="1+ point" fair={r.fair_odds} />
      ))}
    </div>
  );
}

function Shots({ rows }: { rows: ShotRow[] }) {
  if (!rows.length) return <Empty text="No games today." />;
  return (
    <div className="flex flex-col gap-2">
      {rows.map((r, i) => (
        <Row key={r.playerId} rank={i + 1} name={r.player} sub={`${r.team} ${r.opp} · projected ${r.sog_proj.toFixed(1)} shots`}
          meta={`2+ ${pct(r.p_sog2)} (${r.fair2}) · 4+ ${pct(r.p_sog4)} (${r.fair4}) · ${r.sog.toFixed(1)} SOG/gm`}
          big={pct(r.p_sog3)} label="3+ shots" fair={r.fair3} />
      ))}
    </div>
  );
}

const md = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });

function TeamCard({ t }: { t: TeamPick }) {
  return (
    <details className="group rounded-xl border border-white/10 bg-white/[0.03] open:bg-white/[0.05]">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-3 sm:px-4 [&::-webkit-details-marker]:hidden">
        <span className="w-10 shrink-0 rounded-md bg-white/10 py-1 text-center font-mono text-xs font-semibold text-gray-200">{t.team}</span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-white">{t.player}</p>
          <p className="truncate text-xs text-gray-400">{t.opp} · vs {t.opp_goalie}</p>
          <p className="text-[11px] text-gray-500">
            {t.gp} GP · <span className="text-gray-300">{t.g} G</span> · {t.pts} P · {t.sog} SOG · {t.xg.toFixed(1)} xG
          </p>
          <div className="mt-1.5 flex flex-wrap gap-[3px]" aria-label="Goals by game, oldest to newest">
            {[...t.games].reverse().map((g) => (
              <span
                key={g.date}
                title={`${md(g.date)} ${g.opp}: ${g.g} G, ${g.sog} SOG`}
                className={`h-2.5 w-2.5 rounded-sm ${g.g > 1 ? "bg-emerald-300" : g.g === 1 ? "bg-emerald-500" : g.pts > 0 ? "bg-white/25" : "bg-white/10"}`}
              />
            ))}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-mono text-base font-semibold text-emerald-400 tabular-nums">{pct(t.p_goal)}</p>
          <p className="text-[10px] uppercase tracking-wide text-gray-600">Anytime goal</p>
          <p className="font-mono text-[11px] text-gray-400 tabular-nums">Fair {t.fair_odds}</p>
        </div>
        <span className="shrink-0 text-xs text-gray-500 transition-transform group-open:rotate-180">▾</span>
      </summary>
      <div className="overflow-x-auto border-t border-white/10 px-1 pb-2 sm:px-2">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500">
              <th className="px-2 py-2 text-left font-medium">Date</th><th className="px-2 py-2 text-left font-medium">Opp</th>
              <th className="px-2 py-2 text-right font-medium">TOI</th><th className="px-2 py-2 text-right font-medium">PP</th>
              <th className="px-2 py-2 text-right font-medium">SOG</th><th className="px-2 py-2 text-right font-medium">xG</th>
              <th className="px-2 py-2 text-right font-medium">G</th><th className="px-2 py-2 text-right font-medium">P</th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {t.games.map((g) => (
              <tr key={g.date} className={`border-t border-white/5 ${g.g > 0 ? "bg-emerald-400/[0.06]" : ""}`}>
                <td className="px-2 py-1.5 font-sans text-gray-300">{md(g.date)}</td><td className="px-2 py-1.5 font-sans text-gray-400">{g.opp}</td>
                <td className="px-2 py-1.5 text-right">{g.toi.toFixed(1)}</td><td className="px-2 py-1.5 text-right text-gray-400">{g.pp_toi.toFixed(1)}</td>
                <td className="px-2 py-1.5 text-right">{g.sog}</td><td className="px-2 py-1.5 text-right text-gray-400">{g.xg.toFixed(2)}</td>
                <td className={`px-2 py-1.5 text-right ${g.g > 0 ? "font-semibold text-emerald-400" : "text-gray-500"}`}>{g.g}</td>
                <td className="px-2 py-1.5 text-right">{g.pts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function ByTeam({ rows }: { rows: TeamPick[] }) {
  if (!rows.length) return <Empty text="No games today." />;
  return (
    <div>
      <p className="mb-3 text-xs text-gray-400">
        The model&apos;s most likely goal scorer on every team playing tonight. Squares show every game this season, oldest to
        newest — green for a goal, brighter for two or more, grey for a point without a goal. Tap a player for his full game log.
      </p>
      <div className="flex flex-col gap-2">{rows.map((t) => <TeamCard key={t.team} t={t} />)}</div>
    </div>
  );
}

function resultClass(res: string) {
  if (res === "Hit" || res.startsWith("Won")) return "text-emerald-400";
  if (res === "Miss" || res.startsWith("Lost")) return "text-red-400";
  return "text-gray-500";
}

function ParlayCard({ p }: { p: Parlay }) {
  return (
    <Card className="px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-semibold text-white">{p.type}</p>
        <p className="font-mono text-sm tabular-nums">
          <span className="font-semibold text-emerald-400">{pct(p.p, 1)}</span>
          <span className="text-gray-400"> · Fair {p.fair_odds}</span>
        </p>
      </div>
      <ul className="mt-2 space-y-1 text-xs text-gray-300">
        {p.legs.map((l) => (
          <li key={l.playerId} className="flex justify-between gap-3">
            <span className="truncate">{l.player} <span className="text-gray-500">({l.team})</span> · {l.market}</span>
            <span className={`shrink-0 font-mono tabular-nums ${l.result ? resultClass(l.result) : "text-gray-400"}`}>
              {l.result && l.result !== "Did not play" ? l.result : pct(l.p)}
            </span>
          </li>
        ))}
      </ul>
      {p.result !== "Pending" && <p className={`mt-2 text-xs font-semibold ${resultClass(p.result)}`}>{p.result}</p>}
    </Card>
  );
}

function Parlays({ rows }: { rows: Parlay[] }) {
  if (!rows.length) return <Empty text="No parlays today." />;
  return (
    <div>
      <p className="mb-3 text-xs text-gray-400">
        Built from the top of each board, one player per game. Each leg&apos;s house edge stacks in a parlay, so only take one
        priced longer than Fair odds.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">{rows.map((p) => <ParlayCard key={p.type} p={p} />)}</div>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-2 font-medium text-gray-400 ${right ? "text-right" : "text-left"}`}>{children}</th>;
}
function Td({ children, right, className = "" }: { children: React.ReactNode; right?: boolean; className?: string }) {
  return <td className={`px-3 py-2 ${right ? "text-right font-mono tabular-nums" : ""} ${className}`}>{children}</td>;
}

function Record({ s, results, parlays }: { s: Snapshots; results: Pick[]; parlays: Parlay[] }) {
  const graded = s.msum.reduce((a, m) => a + (m.graded || 0), 0);
  const days = Array.from(new Set(results.map((r) => r.pick_date))).slice(0, 3);
  return (
    <div className="flex flex-col gap-5">
      <section>
        <h2 className="mb-1 text-base font-semibold">Record by market</h2>
        <p className="mb-2 text-xs text-gray-400">
          The model&apos;s top 10 each night in each market, graded from official NHL box scores. Expected is the sum of the model&apos;s
          probabilities — over a few hundred picks, Hits should track it.
        </p>
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-white/10"><tr><Th>Market</Th><Th right>Graded</Th><Th right>Hits</Th><Th right>Expected</Th><Th right>Hit rate</Th></tr></thead>
            <tbody>
              {s.msum.map((m) => (
                <tr key={m.market} className="border-b border-white/5 last:border-0">
                  <Td className="font-medium">{m.market}</Td><Td right>{m.graded}</Td><Td right>{m.hits}</Td>
                  <Td right>{m.expected_hits.toFixed(1)}</Td><Td right>{pct(m.hit_rate, 1)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        {graded === 0 && <p className="mt-2 text-xs text-gray-500">No graded picks yet — results post the morning after each slate.</p>}
      </section>

      {s.parlay_summary.length > 0 && (
        <section>
          <h2 className="mb-2 text-base font-semibold">Parlay record</h2>
          <Card className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-white/10"><tr><Th>Type</Th><Th right>Graded</Th><Th right>Won</Th><Th right>Expected</Th><Th right>Win rate</Th></tr></thead>
              <tbody>
                {s.parlay_summary.map((p) => (
                  <tr key={p.parlay} className="border-b border-white/5 last:border-0">
                    <Td className="font-medium">{p.parlay}</Td><Td right>{p.graded}</Td><Td right>{p.won}</Td>
                    <Td right>{p.expected.toFixed(2)}</Td><Td right>{pct(p.win_rate, 1)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </section>
      )}

      {days.length > 0 && (
        <section>
          <h2 className="mb-2 text-base font-semibold">Recent results</h2>
          <div className="flex flex-col gap-3">
            {days.map((d) => (
              <Card key={d} className="px-4 py-3">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">{d}</p>
                <ul className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                  {results.filter((r) => r.pick_date === d).map((r) => (
                    <li key={`${r.market}-${r.player}`} className="flex justify-between gap-3">
                      <span className="truncate text-gray-300">{r.player} <span className="text-gray-500">· {r.market} · {pct(r.p)}</span></span>
                      <span className={`shrink-0 font-semibold ${resultClass(r.result)}`}>{r.result}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        </section>
      )}

      {parlays.length > 0 && (
        <section>
          <h2 className="mb-2 text-base font-semibold">Recent parlays</h2>
          <div className="grid gap-2 sm:grid-cols-2">{parlays.slice(0, 10).map((p) => <ParlayCard key={`${p.parlay_date}-${p.type}`} p={p} />)}</div>
        </section>
      )}

      <section>
        <h2 className="mb-1 text-base font-semibold">Backtest — 2025-26 season</h2>
        <p className="mb-2 text-xs text-gray-400">Models trained on 2024-25 only, then scored on every 2025-26 game they never saw.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Card className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-white/10"><tr><Th>Top picks</Th><Th right>Predicted</Th><Th right>Actual</Th></tr></thead>
              <tbody>
                {s.top.filter((t) => t.picks === "Top 1 per night" || t.picks === "Top 5 per night").map((t) => (
                  <tr key={t.key} className="border-b border-white/5 last:border-0">
                    <Td>{t.market} · {t.picks.replace(" per night", "")}</Td><Td right>{t.predicted_pct.toFixed(1)}%</Td><Td right className="text-emerald-400">{t.actual_pct.toFixed(1)}%</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-white/10"><tr><Th>Parlay</Th><Th right>Predicted</Th><Th right>Actual</Th></tr></thead>
              <tbody>
                {s.parlay_bt.map((b) => (
                  <tr key={b.parlay} className="border-b border-white/5 last:border-0">
                    <Td>{b.parlay}</Td><Td right>{b.predicted_pct.toFixed(1)}%</Td><Td right className="text-emerald-400">{b.actual_pct.toFixed(1)}%</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      </section>
    </div>
  );
}

export default async function NhlPage() {
  let s: Snapshots | null = null, parlays: Parlay[] = [], results: Pick[] = [], recentParlays: Parlay[] = [], error: string | null = null;
  try {
    s = await getSnapshots();
    const date = s.today[0]?.date || s.summary?.run_date;
    [parlays, results, recentParlays] = await Promise.all([
      date ? getParlays(date) : Promise.resolve([]), getRecentResults(), getRecentParlays(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Couldn't load NHL data.";
  }
  const date = s?.today[0]?.date || s?.summary?.run_date;
  const ch = s?.summary?.champions;
  const nice = date ? new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }) : "";

  return (
    <div>
      <div className="mb-5">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-bold sm:text-2xl">NHL Props Board{nice && <span className="text-gray-400"> · {nice}</span>}</h1>
          {ch && (
            <span className="rounded-full bg-emerald-400/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-emerald-300">
              MODELS {ch.goal} · {ch.point} · {ch.sog}
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-gray-400 sm:text-sm">
          Chance each player scores, records a point, or reaches a shots line — with the break-even odds. A bigger plus number
          (or a smaller minus) at your book than Fair means value; anything else, pass.
          {s?.summary?.data_through && <> Stats through {s.summary.data_through}.</>} Goalies are Daily Faceoff&apos;s projected
          starters; * = no projection yet, so the model guessed.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {s && (
        <Tabs
          tabs={[
            { id: "goals", label: "Goals", content: <Goals rows={s.today} /> },
            { id: "teams", label: "By team", content: <ByTeam rows={s.team_picks} /> },
            { id: "points", label: "Points", content: <Points rows={s.today_pts} /> },
            { id: "shots", label: "Shots", content: <Shots rows={s.today_sog} /> },
            { id: "parlays", label: "Parlays", content: <Parlays rows={parlays} /> },
            { id: "record", label: "Record", content: <Record s={s} results={results} parlays={recentParlays} /> },
          ]}
        />
      )}

      <p className="mt-8 text-center text-[11px] leading-relaxed text-gray-600">
        Model estimates for analysis and entertainment, not betting advice. Must be 21+ where sports betting is legal.
        If gambling is a problem, call 1-800-GAMBLER. NHL data from MoneyPuck and NHL.com; not affiliated with the NHL.
      </p>
    </div>
  );
}
