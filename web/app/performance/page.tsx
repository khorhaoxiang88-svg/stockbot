import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ScopeDisclosure } from "@/components/scope-disclosure";
import {
  EXECUTION_PROTOCOL_VERSION,
  getActiveExperiment,
  getBooks,
  getCancelledEntries,
  getOfficialBenchmarkPositions,
  getOfficialPaperPositions,
  getPreLaunchBenchmarkPositions,
  getPreLaunchPaperPositions,
  getUniqueOriginatingCandidateCount,
  type BenchmarkPosition,
  type Book,
  type PaperPosition,
} from "@/lib/db";
import { formatEastern } from "@/lib/time";
import {
  averageWinLoss,
  evidenceBand,
  maxDrawdown,
  observationWindow,
  pendingAsProvisionalTrades,
  profitFactor,
  sectorConcentration,
  wilsonInterval,
  type ClosedTrade,
} from "@/lib/performance";

/** O3's exact timeline statement, displayed once under "Official results". */
const TIMELINE_STATEMENT =
  "The earliest status above preliminary evidence is 12 months after the " +
  "official experiment begins, provided at least 100 positions have closed " +
  "in that horizon.";

/**
 * Per horizon, never pooled. The 20-day and 60-day books are separate
 * simulated strategy variants over the same candidates, so every statistic on
 * this page is computed twice, independently, and never blended into one
 * combined number.
 *
 * "Engineering validation dataset — not strategy performance" used to be a
 * root-layout banner shown unconditionally on every page (phase-banner.tsx,
 * removed) -- wrong once official results existed, since it contradicted the
 * live ExperimentBanner on the very same pages and falsely implied even
 * /health and /changelog were "one execution against a 50-security fixture."
 * It is now scoped precisely to where it's actually true: the Pre-launch
 * results section below, the only place fixture-only numbers are shown as
 * results rather than as membership/coverage metadata.
 */

export const dynamic = "force-dynamic";

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
      {children}
    </p>
  );
}

function money(value: number): string {
  const sign = value < 0 ? "−" : "";
  return `${sign}$${Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function pct(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

function toClosedTrades(rows: PaperPosition[]): ClosedTrade[] {
  return rows
    .filter((r) => r.status === "closed" && r.exit_date && r.net_pnl !== null)
    .map((r) => ({
      exit_date: r.exit_date as string,
      net_pnl: r.net_pnl as number,
      gross_pnl: (r.gross_pnl as number) ?? 0,
      pnl_pct: (r.pnl_pct as number) ?? 0,
      cohort_id: r.cohort_id,
    }));
}

function HorizonSection({
  horizon,
  book,
  positions,
  benchmarks,
  cancelledForHorizon,
  isOfficial,
  experimentStartedAt,
}: {
  horizon: number;
  book: Book | undefined;
  positions: PaperPosition[];
  benchmarks: BenchmarkPosition[];
  cancelledForHorizon: number;
  isOfficial: boolean;
  experimentStartedAt: string | null;
}) {
  const closed = positions.filter((p) => p.status === "closed");
  const pending = positions.filter((p) => p.status === "pending_resolution");
  const open = positions.filter((p) => p.status === "open");
  const trades = toClosedTrades(closed);
  const window = observationWindow(trades);
  const sectors = sectorConcentration(trades);

  const pendingNotional = pending.reduce((s, p) => s + p.notional, 0);
  const asOfDate = new Date().toISOString().slice(0, 10);
  const provisionalPendingTrades = pendingAsProvisionalTrades(
    pending.map((p) => ({
      notional: p.notional,
      dividends_received: p.dividends_received,
      cohort_id: p.cohort_id,
    })),
    asOfDate,
  );
  const tradesIncludingPending = [...trades, ...provisionalPendingTrades];

  function statsFor(set: ClosedTrade[]) {
    const wins = set.filter((t) => t.net_pnl > 0).length;
    return {
      n: set.length,
      wins,
      wilson: wilsonInterval(wins, set.length),
      winLoss: averageWinLoss(set),
      pf: profitFactor(set),
      drawdown: book ? maxDrawdown(set, book.starting_nav) : { drawdownPct: 0, troughNav: 0, peakNav: 0 },
    };
  }

  // R1-PROTOCOL-1.1 requires both views side by side: a pending-resolution
  // position is neither a win nor a loss yet, so EXCLUDING it is the honest
  // default, and INCLUDING it at the provisional zero policy value shows what
  // the headline numbers would look like if every unresolved delisting is
  // eventually written off. Neither view is hidden behind the other.
  const excluding = statsFor(trades);
  const including = statsFor(tradesIncludingPending);
  const hasPending = pending.length > 0;

  // O3: computed from OFFICIAL closed positions only. `excluding.n` is
  // already scoped correctly by the caller -- isOfficial=true is only ever
  // paired with positions drawn from getOfficialPaperPositions -- and
  // deliberately uses `excluding`, never `including`: a pending position
  // valued at its provisional zero is not evidence of anything yet, so it
  // must not inflate the count that decides how much evidence exists.
  const band = isOfficial ? evidenceBand(excluding.n, experimentStartedAt, asOfDate) : null;

  const benchmarkByCandidate = new Map(
    benchmarks.filter((b) => b.status === "closed").map((b) => [b.candidate_id, b]),
  );

  const attempted = positions.length + cancelledForHorizon;
  const cancellationRate = attempted > 0 ? cancelledForHorizon / attempted : null;

  return (
    <section className="mb-16 space-y-6 border-t border-border pt-10 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center gap-3">
        <h2>{horizon}-day book</h2>
        <Badge variant="outline" className="px-3 py-1 font-mono">
          {book?.book_id ?? `book-${horizon}d`}
        </Badge>
      </div>

      {band ? (
        <div
          className={`rounded-lg border px-4 py-3 text-sm ${
            band.tier === "evaluation_possible"
              ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-100"
              : band.tier === "preliminary"
                ? "border-amber-400/40 bg-amber-400/10 text-amber-100"
                : "border-border bg-muted/40 text-muted-foreground"
          }`}
        >
          <span className="text-base font-semibold">{band.label}</span>
          <span className="ml-2 font-mono text-xs">
            {excluding.n} official closed position{excluding.n === 1 ? "" : "s"} at this
            horizon
          </span>
          <p className="mt-1 text-xs">
            A null result — no detectable edge — reads exactly like this: an honest
            evidence band and the full statistics below it, not a missing feature or a
            problem awaiting a fix.
          </p>
        </div>
      ) : null}

      {hasPending ? (
        <p className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-4 py-2 text-sm text-amber-100">
          {pending.length} position{pending.length === 1 ? "" : "s"} pending resolution.
          Every statistic below is reported <strong>excluding</strong> them AND{" "}
          <strong>including</strong> them at their provisional zero policy value —
          never one view alone.
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Sample size</div>
          <div className="mt-1 font-mono text-2xl">
            {excluding.n}
            {hasPending ? <span className="text-muted-foreground"> / {including.n}</span> : null}
          </div>
          <div className="text-xs text-muted-foreground">
            closed{hasPending ? " / including pending" : ""}
          </div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Observation window</div>
          <div className="mt-1 font-mono text-sm">
            {window ? `${window.start} → ${window.end}` : "—"}
          </div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Win rate (95% Wilson CI)</div>
          <div className="mt-1 font-mono text-2xl">
            {excluding.n ? `${excluding.wins}/${excluding.n}` : "—"}
          </div>
          <div className="font-mono text-xs text-muted-foreground">
            {excluding.wilson
              ? `${pct(excluding.wilson.lower)} – ${pct(excluding.wilson.upper)}`
              : "n too small for an interval"}
            {" · excluding pending"}
          </div>
          {hasPending ? (
            <div className="font-mono text-xs text-amber-200">
              {including.n ? `${including.wins}/${including.n}` : "—"}{" "}
              {including.wilson
                ? `(${pct(including.wilson.lower)} – ${pct(including.wilson.upper)})`
                : ""}{" "}
              · including pending @ zero
            </div>
          ) : null}
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Profit factor</div>
          <div className="mt-1 font-mono text-2xl">
            {excluding.pf.defined ? excluding.pf.value.toFixed(2) : "—"}
          </div>
          {!excluding.pf.defined ? (
            <div className="text-xs text-amber-200">{excluding.pf.reason}</div>
          ) : null}
          {hasPending ? (
            <div className="text-xs text-muted-foreground">
              {including.pf.defined ? including.pf.value.toFixed(2) : "—"} including pending
            </div>
          ) : null}
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Average win / loss</div>
          <div className="mt-1 font-mono text-lg">
            {excluding.winLoss.avgWin === null ? "—" : money(excluding.winLoss.avgWin)} /{" "}
            {excluding.winLoss.avgLoss === null ? "—" : money(excluding.winLoss.avgLoss)}
          </div>
          <div className="text-xs text-muted-foreground">
            {excluding.winLoss.winCount}W · {excluding.winLoss.lossCount}L
            {excluding.winLoss.scratchCount ? ` · ${excluding.winLoss.scratchCount} scratch` : ""}
          </div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">
            Max drawdown (against fixed ${book?.starting_nav.toLocaleString() ?? "100,000"} NAV)
          </div>
          <div className="mt-1 font-mono text-2xl">{pct(excluding.drawdown.drawdownPct, 2)}</div>
          <div className="text-xs text-muted-foreground">excluding pending</div>
          {hasPending ? (
            <div className="mt-1 font-mono text-sm text-amber-200">
              {pct(including.drawdown.drawdownPct, 2)} including pending @ zero
            </div>
          ) : null}
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Current NAV / open positions</div>
          <div className="mt-1 font-mono text-lg">
            {book ? money(book.current_nav) : "—"}
          </div>
          <div className="text-xs text-muted-foreground">
            {book?.open_position_count ?? 0} open · {pending.length} pending resolution
          </div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Cancelled entries (this horizon)</div>
          <div className="mt-1 font-mono text-2xl">{cancelledForHorizon}</div>
          <div className="text-xs text-muted-foreground">
            {cancellationRate === null ? "—" : pct(cancellationRate)} of attempts
          </div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="text-xs text-muted-foreground">Pending-resolution notional</div>
          <div className="mt-1 font-mono text-2xl">{money(pendingNotional)}</div>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-lg font-semibold">Sector concentration (SIC-derived)</h3>
        {sectors.length === 0 ? (
          <EmptyState>No closed trades yet to concentrate.</EmptyState>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {sectors.map((s) => (
              <li
                key={s.cohort}
                className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm"
              >
                <span className="font-mono">{s.cohort}</span>
                <span className="text-muted-foreground">
                  {s.count} ({pct(s.pct)})
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-3">
        <h3 className="text-lg font-semibold">Closed trades</h3>
        <p className="text-sm text-muted-foreground">
          Losses are shown identically to wins — same columns, same styling, no
          separate table.
        </p>
        {closed.length === 0 ? (
          <EmptyState>No closed trades yet for this horizon.</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Security</TableHead>
                  <TableHead>Entry</TableHead>
                  <TableHead>Exit</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead className="text-right">Net P&amp;L</TableHead>
                  <TableHead className="text-right">Pct</TableHead>
                  <TableHead className="text-right">Paired SPY</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {closed.map((p) => {
                  const bench = benchmarkByCandidate.get(p.candidate_id);
                  return (
                    <TableRow key={p.position_id}>
                      <TableCell>
                        <Link
                          href={`/security/${p.security_id}`}
                          className="underline underline-offset-4"
                        >
                          {p.symbol ?? p.security_id}
                        </Link>
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {p.entry_date} @ {p.entry_price.toFixed(2)}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {p.exit_date} @ {(p.exit_price ?? 0).toFixed(2)}
                      </TableCell>
                      <TableCell className="text-sm">{p.exit_reason}</TableCell>
                      <TableCell
                        className={`text-right font-mono ${
                          (p.net_pnl ?? 0) < 0 ? "text-red-300" : "text-emerald-300"
                        }`}
                      >
                        {money(p.net_pnl ?? 0)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {pct(p.pnl_pct ?? 0, 2)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-muted-foreground">
                        {bench ? pct((bench.pnl_pct as number) ?? 0, 2) : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {open.length || pending.length ? (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold">Open and pending positions</h3>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Security</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Entry</TableHead>
                  <TableHead className="text-right">Stop</TableHead>
                  <TableHead className="text-right">Target</TableHead>
                  <TableHead className="text-right">Notional</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...open, ...pending].map((p) => (
                  <TableRow key={p.position_id}>
                    <TableCell>
                      <Link
                        href={`/security/${p.security_id}`}
                        className="underline underline-offset-4"
                      >
                        {p.symbol ?? p.security_id}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={
                          p.status === "pending_resolution"
                            ? "border-amber-400/40 bg-amber-400/15 text-amber-100"
                            : "border-sky-400/40 bg-sky-400/15 text-sky-100"
                        }
                      >
                        {p.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {p.entry_date} @ {p.entry_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {p.stop_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {p.target_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {money(p.notional)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {pending.length ? (
            <p className="text-xs text-muted-foreground">
              Pending-resolution positions never close at their last quoted price.
              They stay in reported exposure until a verified recovery or 180 days
              elapse, at which point the recorded resolution policy values the
              common equity at zero.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export default async function PerformancePage() {
  const books = getBooks();
  const cancelled = getCancelledEntries();
  const uniqueCandidates = getUniqueOriginatingCandidateCount();
  const { row: experiment } = getActiveExperiment();
  const horizons = books.rows.length ? books.rows.map((b) => b.horizon_days) : [20, 60];

  const perHorizonOfficial = horizons.map((horizon) => ({
    horizon,
    book: books.rows.find((b) => b.horizon_days === horizon),
    positions: getOfficialPaperPositions(horizon).rows,
    benchmarks: getOfficialBenchmarkPositions(horizon).rows,
  }));
  const perHorizonPreLaunch = horizons.map((horizon) => ({
    horizon,
    book: books.rows.find((b) => b.horizon_days === horizon),
    positions: getPreLaunchPaperPositions(horizon).rows,
    benchmarks: getPreLaunchBenchmarkPositions(horizon).rows,
  }));

  const totalPreLaunchPositions = perHorizonPreLaunch.reduce((s, h) => s + h.positions.length, 0);

  // A cancellation applies to every horizon a candidate was permitted into, but
  // cancelled_entries itself carries no horizon — it is per candidate, logged
  // once. Reported per horizon here as "cancelled before this book could open
  // a position", which is what a cancellation means for every book equally.
  // Cancellations pre-date official/pre-launch separation in the schema (no
  // candidate has ever been cancelled under an official run yet), so the same
  // total is shown against both sections rather than invented as zero.
  const cancelledByHorizon = new Map<number, number>();
  for (const horizon of horizons) cancelledByHorizon.set(horizon, cancelled.rows.length);

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="mb-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
        <Link href="/health" className="underline underline-offset-4">
          stockbot
        </Link>{" "}
        / performance
      </p>
      <h1 className="mb-2">Performance</h1>

      <ScopeDisclosure />

      <div className="mb-10 flex flex-wrap items-center gap-4">
        <Badge variant="outline" className="px-3 py-1 text-lg">
          {uniqueCandidates} unique originating candidate{uniqueCandidates === 1 ? "" : "s"}
        </Badge>
        <p className="text-sm text-muted-foreground">
          The {horizons.length} books below are separate strategy variants over
          the <strong>same</strong> candidates — never pooled, and never read as{" "}
          {uniqueCandidates * horizons.length} independent observations.
        </p>
      </div>

      <section className="mb-14">
        <h2 className="mb-3">Official results</h2>
        {experiment ? (
          <p className="mb-3 text-sm text-muted-foreground">
            Experiment <span className="font-mono">{experiment.experiment_id}</span>, live
            since {formatEastern(experiment.started_at)} (strategy v
            {experiment.strategy_version}). Only candidates generated at or after that
            instant, under a genuine official run, ever appear below —
            official_candidates / official_positions (migration 022) enforce this at
            the query layer, not by convention.
          </p>
        ) : (
          <p className="mb-3 text-sm text-muted-foreground">
            No official experiment has launched yet (pipeline/launch/open_experiment.py
            has not run). Zero official trades is the correct state, not a missing
            feature.
          </p>
        )}
        <p className="mb-6 text-sm text-muted-foreground">{TIMELINE_STATEMENT}</p>
        {
          // Always rendered, even at zero official closed positions: an
          // evidence band of "Insufficient evidence" IS the correct, complete
          // answer at n=0, not a state that gets swallowed by a generic empty
          // message. See O3's null-result requirement.
          perHorizonOfficial.map((h) => (
            <HorizonSection
              key={h.horizon}
              horizon={h.horizon}
              book={h.book}
              positions={h.positions}
              benchmarks={h.benchmarks}
              cancelledForHorizon={0}
              isOfficial
              experimentStartedAt={experiment?.started_at ?? null}
            />
          ))
        }
      </section>

      <section className="border-t border-border pt-10">
        <h2 className="mb-3 text-muted-foreground">
          Pre-launch results (Phase F fixture / Phase S paper trades)
        </h2>
        <div className="mb-6 rounded-lg border border-amber-400/40 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          <strong>Engineering validation dataset — not strategy performance.</strong>{" "}
          Every number in this section comes from one execution of a frozen
          protocol (<span className="font-mono">{EXECUTION_PROTOCOL_VERSION}</span>)
          against the Phase F 50-security fixture. It is not a backtest of a
          trading strategy and must not be read as one.
        </div>
        <p className="mb-6 text-sm text-muted-foreground">
          Permanently excluded from official statistics — never merged, backfilled or
          retroactively counted into the section above, no matter what happens later.
          Shown here only as engineering validation of the protocol itself.
        </p>
        {totalPreLaunchPositions === 0 && cancelled.rows.length === 0 ? (
          <EmptyState>
            No data yet. Zero candidates have been selected for execution (the
            current weekly selection produced none — see /candidates for why), so
            there is nothing to execute or resolve. This is the correct state of
            the protocol proving its own gates, not a missing feature.
          </EmptyState>
        ) : (
          perHorizonPreLaunch.map((h) => (
            <HorizonSection
              key={h.horizon}
              horizon={h.horizon}
              book={h.book}
              positions={h.positions}
              benchmarks={h.benchmarks}
              cancelledForHorizon={cancelledByHorizon.get(h.horizon) ?? 0}
              isOfficial={false}
              experimentStartedAt={null}
            />
          ))
        )}
      </section>
    </main>
  );
}
