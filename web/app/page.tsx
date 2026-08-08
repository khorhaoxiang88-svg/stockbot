import Link from "next/link";

import { getActiveExperiment } from "@/lib/db";
import { formatEastern } from "@/lib/time";

/**
 * The status line here is derived from the database, not hand-written prose
 * describing "the current phase" -- that went stale for months (still said
 * "Phase 1: project skeleton... No market data yet" through S1-O3) because
 * nothing forced it to be touched when a phase shipped. Deriving it from
 * getActiveExperiment() means it can only ever be wrong in the same way the
 * rest of the site would be wrong, not silently on its own schedule.
 */
export const dynamic = "force-dynamic";

const LINKS = [
  { href: "/performance", label: "Performance", description: "Official results, evidence bands, statistics" },
  { href: "/candidates", label: "Research candidates", description: "This week's selection and the suppression log" },
  { href: "/universe", label: "Universe", description: "Membership snapshots and coverage" },
  { href: "/health", label: "System health", description: "Pipeline run history, source freshness, migrations" },
  { href: "/changelog", label: "Changelog", description: "Published defects and the bug-correction policy" },
];

export default async function Home() {
  const { row: experiment } = getActiveExperiment();

  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-24">
      <p className="mb-4 text-base uppercase tracking-[0.2em] text-muted-foreground">
        stockbot
      </p>
      <h1 className="mb-6">US stock research system</h1>
      <p className="mb-12 max-w-3xl text-muted-foreground">
        {experiment ? (
          <>
            Official forward experiment live since{" "}
            {formatEastern(experiment.started_at)} (strategy v
            {experiment.strategy_version}). Evidence is still accumulating
            — see <Link href="/performance" className="underline underline-offset-4 hover:text-foreground">/performance</Link>{" "}
            for the current evidence band, per horizon.
          </>
        ) : (
          "No official experiment has launched yet. Every result on this " +
          "site is pre-launch (Phase F fixture / Phase S paper trades)."
        )}
      </p>
      <nav className="grid gap-4 sm:grid-cols-2">
        {LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="rounded-lg border border-border px-6 py-4 hover:bg-muted"
          >
            <div className="font-medium">{link.label}</div>
            <div className="text-sm text-muted-foreground">{link.description}</div>
          </Link>
        ))}
      </nav>
    </main>
  );
}
