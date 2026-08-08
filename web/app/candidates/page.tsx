import Link from "next/link";

import { ScopeDisclosure } from "@/components/scope-disclosure";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  SUPPRESSION_LABELS,
  getBooks,
  getCandidatesForRun,
  getLatestSelectionRun,
  getPublishedSelectionRun,
  getStaleSourceReason,
  getSuppressionsForRun,
  type SuppressedSignal,
} from "@/lib/db";
import { formatEastern } from "@/lib/time";

/**
 * This week's research candidates, and everything that was considered and not
 * selected.
 *
 * The suppression log is not an appendix. A candidate list on its own cannot be
 * audited: there is no way to tell a security that failed a rule from one the
 * code never looked at. Both halves are rendered at the same weight, and the
 * page states the count of unique originating candidates so the two books are
 * never read as twice the sample.
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
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

type HealthEntry = { source: string; ok: boolean; detail: string };

function SuppressionGroup({
  reason,
  rows,
}: {
  reason: string;
  rows: SuppressedSignal[];
}) {
  const unique = new Set(rows.map((row) => row.security_id));
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 className="text-lg font-semibold">
          {SUPPRESSION_LABELS[reason] ?? reason}
        </h3>
        <Badge variant="outline" className="px-3 py-1 font-mono">
          {unique.size} securit{unique.size === 1 ? "y" : "ies"} · {rows.length} row
          {rows.length === 1 ? "" : "s"}
        </Badge>
      </div>
      <p className="font-mono text-xs text-muted-foreground">{reason}</p>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Security</TableHead>
              <TableHead className="text-right">Horizon</TableHead>
              <TableHead className="text-right">Composite</TableHead>
              <TableHead className="text-right">Rank</TableHead>
              <TableHead>Why</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={`${row.security_id}-${row.horizon_days}-${reason}`}>
                <TableCell>
                  <Link
                    href={`/security/${row.security_id}`}
                    className="underline underline-offset-4"
                  >
                    {row.symbol ?? row.security_id}
                  </Link>
                </TableCell>
                <TableCell className="text-right font-mono">
                  {row.horizon_days}d
                </TableCell>
                <TableCell className="text-right font-mono">
                  {row.composite === null ? "—" : row.composite.toFixed(4)}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {row.rank ?? "—"}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {row.detail}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

export default async function CandidatesPage() {
  const latestAttempt = getLatestSelectionRun();
  const run = getPublishedSelectionRun();
  const isStale = Boolean(
    latestAttempt.row && run.row && latestAttempt.row.run_id !== run.row.run_id,
  );
  const staleReason = isStale && latestAttempt.row
    ? getStaleSourceReason(latestAttempt.row.run_id)
    : null;
  const candidates = run.row ? getCandidatesForRun(run.row.run_id) : { rows: [] };
  const suppressions = run.row
    ? getSuppressionsForRun(run.row.run_id)
    : { rows: [] as SuppressedSignal[] };
  const books = getBooks();

  const grouped = new Map<string, SuppressedSignal[]>();
  for (const row of suppressions.rows) {
    const list = grouped.get(row.suppression_reason) ?? [];
    list.push(row);
    grouped.set(row.suppression_reason, list);
  }

  const first = candidates.rows[0];
  const health: HealthEntry[] = first
    ? (JSON.parse(first.source_health_snapshot_json).sources ?? [])
    : [];

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="mb-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
        <Link href="/health" className="underline underline-offset-4">
          stockbot
        </Link>{" "}
        / candidates
      </p>
      <h1 className="mb-2">Research candidates</h1>
      <p className="mb-10 text-muted-foreground">
        Selection runs once per US trading week, after that week&rsquo;s final
        regular session closes. It is fully automatic: nothing on this page can be
        added, removed or reordered by hand, and every stored candidate carries a
        hash over all of its fields so an edit is detectable rather than merely
        discouraged.
      </p>

      <ScopeDisclosure />

      {isStale && latestAttempt.row ? (
        <div className="mb-10 rounded-lg border border-amber-400/40 bg-amber-400/10 p-5 text-sm text-amber-100">
          <p className="font-semibold">
            Screener stale — the most recent selection run ({formatEastern(latestAttempt.row.started_at)})
            was blocked by a required source failure and generated no new candidates.
          </p>
          {staleReason ? <p className="mt-1 font-mono text-xs">{staleReason}</p> : null}
          <p className="mt-2">
            {run.row
              ? `Showing the last published screener, from ${formatEastern(run.row.started_at)}.`
              : "No screener has ever been successfully published."}
          </p>
        </div>
      ) : null}

      {!run.row ? (
        <EmptyState>
          {latestAttempt.row
            ? "No selection has ever successfully published candidates — every run so far " +
              "was blocked by a required source failure. See the warning above."
            : "No data yet. Run pipeline/selection/compute.py to produce a weekly selection."}
        </EmptyState>
      ) : (
        <>
          <section className="mb-14 space-y-4">
            <h2>This week</h2>
            <div className="flex flex-wrap items-center gap-4">
              <Badge
                variant="outline"
                className={
                  candidates.rows.length
                    ? "border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-lg text-emerald-200"
                    : "border-amber-400/40 bg-amber-400/15 px-3 py-1 text-lg text-amber-200"
                }
              >
                {candidates.rows.length} candidate
                {candidates.rows.length === 1 ? "" : "s"}
              </Badge>
              <Badge variant="outline" className="px-3 py-1 font-mono">
                run {run.row.run_id}
              </Badge>
              <Badge variant="outline" className="px-3 py-1 font-mono">
                {run.row.status}
              </Badge>
              {first ? (
                <Badge variant="outline" className="px-3 py-1 font-mono">
                  evidence cutoff {first.data_cutoff_at}
                </Badge>
              ) : null}
            </div>
            <p className="text-sm text-muted-foreground">
              Generated {formatEastern(run.row.started_at)}. The two books are
              separate strategy variants over the <strong>same</strong> candidates,
              so this week produced {candidates.rows.length} unique originating
              candidate{candidates.rows.length === 1 ? "" : "s"}
              {candidates.rows.length > 0
                ? ` — not ${candidates.rows.length * books.rows.length} observations.`
                : "."}
            </p>

            {candidates.rows.length === 0 ? (
              <EmptyState>
                No candidate was selected. Every security considered is in the
                suppression log below with its reason — an empty selection is a
                result, not a gap.
              </EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-right">#</TableHead>
                      <TableHead>Security</TableHead>
                      <TableHead className="text-right">Composite</TableHead>
                      <TableHead className="text-right">Signal close</TableHead>
                      <TableHead className="text-right">ATR</TableHead>
                      <TableHead>Entry rule</TableHead>
                      <TableHead>Provenance</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {candidates.rows.map((candidate) => {
                      const snapshot = JSON.parse(candidate.score_snapshot_json);
                      const accessions: string[] = JSON.parse(
                        candidate.accessions_used_json,
                      );
                      return (
                        <TableRow key={candidate.candidate_id}>
                          <TableCell className="text-right font-mono">
                            {candidate.rank_at_generation}
                          </TableCell>
                          <TableCell>
                            <Link
                              href={`/security/${candidate.security_id}`}
                              className="font-medium underline underline-offset-4"
                            >
                              {candidate.symbol ?? candidate.security_id}
                            </Link>
                            <div className="text-xs text-muted-foreground">
                              {snapshot.cohort_id}
                            </div>
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {candidate.composite_at_generation.toFixed(4)}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {candidate.signal_close.toFixed(2)}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {candidate.atr_value === null
                              ? "—"
                              : candidate.atr_value.toFixed(3)}
                            <div className="text-xs text-muted-foreground">
                              {candidate.atr_window}d
                            </div>
                          </TableCell>
                          <TableCell className="text-sm">
                            {candidate.entry_rule}
                            <div className="text-xs text-muted-foreground">
                              gap limit {candidate.gap_limit_atr} ATR
                            </div>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            <div>strategy v{candidate.strategy_version}</div>
                            <div>rule v{candidate.selection_rule_version}</div>
                            <div>config {candidate.config_hash.slice(0, 12)}</div>
                            <div>
                              price dataset v{candidate.price_dataset_version ?? "—"}
                            </div>
                            <div>snapshot {candidate.snapshot_id}</div>
                            <div>{accessions.length} accessions used</div>
                            <div>row hash {candidate.row_hash.slice(0, 12)}</div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>

          {health.length ? (
            <section className="mb-14 space-y-4">
              <h2>Source freshness at the cutoff</h2>
              <ul className="grid gap-2">
                {health.map((entry) => (
                  <li
                    key={entry.source}
                    className={`rounded-lg border p-3 text-sm ${
                      entry.ok
                        ? "border-emerald-400/30 bg-emerald-400/10"
                        : "border-red-400/40 bg-red-400/10"
                    }`}
                  >
                    <span className="font-mono">{entry.source}</span> — {entry.detail}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="mb-14 space-y-6">
            <h2>Suppression log</h2>
            <p className="text-sm text-muted-foreground">
              Everything considered and not selected, with the reason. Rows are per
              book, so a security blocked only at one horizon appears once. A
              qualifying signal for a security already held at that horizon is
              logged here rather than discarded.
            </p>
            {grouped.size === 0 ? (
              <EmptyState>Nothing was suppressed in this run.</EmptyState>
            ) : (
              [...grouped.keys()]
                .sort()
                .map((reason) => (
                  <SuppressionGroup
                    key={reason}
                    reason={reason}
                    rows={grouped.get(reason) ?? []}
                  />
                ))
            )}
          </section>

          <section className="mb-14 space-y-4">
            <h2>Books</h2>
            <p className="text-sm text-muted-foreground">
              An experimental accounting convention, <strong>not</strong> recommended
              position sizing. Every position takes the same fixed notional whatever
              its price or volatility, nothing compounds during Release 1, and cash
              earns no interest.
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Book</TableHead>
                  <TableHead className="text-right">Horizon</TableHead>
                  <TableHead className="text-right">Starting NAV</TableHead>
                  <TableHead className="text-right">Current NAV</TableHead>
                  <TableHead className="text-right">Open positions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {books.rows.map((book) => (
                  <TableRow key={book.book_id}>
                    <TableCell className="font-mono">{book.book_id}</TableCell>
                    <TableCell className="text-right font-mono">
                      {book.horizon_days}d
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {money(book.starting_nav)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {money(book.current_nav)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {book.open_position_count}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="text-sm text-muted-foreground">
              The books are separate simulated strategy variants. They are not two
              independent observations and not twice the sample; never pool them.
            </p>
          </section>
        </>
      )}
    </main>
  );
}
