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

type FilterState = "pass" | "fail" | "unknown";

type FilterCheck = {
  label: string;
  state: FilterState;
};

const FILTER_MARKS: Record<FilterState, { mark: string; word: string }> = {
  pass: { mark: "✓", word: "Passed" },
  fail: { mark: "✕", word: "Failed" },
  unknown: { mark: "?", word: "Missing evidence" },
};

/**
 * Turn the pipeline's first-failure reason into a truthful, readable checklist.
 * A rule before the recorded failure passed; later rules were not evaluated.
 * Missing evidence is deliberately not shown as a pass or a fail.
 */
function filterChecks(row: SuppressedSignal): FilterCheck[] {
  const detail = row.detail ?? "";

  if (row.suppression_reason === "dilution_or_riskflags_unknown") {
    const missingDilution = detail.includes("dilution_signals");
    const missingRiskFlags = detail.includes("risk_flags");
    return [
      { label: "In stock universe", state: "pass" },
      {
        label: "Composite score",
        state: row.composite === null ? "unknown" : "pass",
      },
      {
        label: "Dilution evidence",
        state: missingDilution ? "unknown" : "pass",
      },
      {
        label: "Risk-flag evidence",
        state: missingRiskFlags ? "unknown" : "pass",
      },
      { label: "Final eligibility", state: "fail" },
    ];
  }

  const filters: Array<FilterCheck & { reasons: string[] }> = [
    { label: "Fresh market data", state: "unknown", reasons: ["stale_source"] },
    { label: "Composite score", state: "unknown", reasons: ["not_rankable"] },
    { label: "Model supported", state: "unknown", reasons: ["model_not_applicable"] },
    { label: "Dilution screen", state: "unknown", reasons: ["dilution_disqualified"] },
    {
      label: "Risk-flag screen",
      state: "unknown",
      reasons: ["risk_flag_going_concern", "risk_flag_dilution_disqualify"],
    },
    {
      label: "Minimum score",
      state: "unknown",
      reasons: ["composite_threshold_unset", "below_composite_threshold"],
    },
    {
      label: "Cooldown clear",
      state: "unknown",
      reasons: ["cooldown_recent_exit", "cooldown_gap_cancelled"],
    },
    {
      label: "Portfolio limits",
      state: "unknown",
      reasons: ["open_position", "book_capacity", "selection_cap", "cohort_cap"],
    },
  ];

  const failedAt = filters.findIndex((filter) =>
    filter.reasons.includes(row.suppression_reason),
  );
  return filters.map(({ label }, index) => ({
    label,
    state: failedAt === -1 ? "unknown" : index < failedAt ? "pass" : index === failedAt ? "fail" : "unknown",
  }));
}

function SuppressionGroup({
  reason,
  rows,
}: {
  reason: string;
  rows: SuppressedSignal[];
}) {
  const unique = new Set(rows.map((row) => row.security_id));
  const stocks = new Map<number, SuppressedSignal[]>();
  for (const row of rows) {
    const stockRows = stocks.get(row.security_id) ?? [];
    stockRows.push(row);
    stocks.set(row.security_id, stockRows);
  }

  return (
    <details className="suppression-card">
      <summary>
        <span className="summary-copy">
          <strong>{SUPPRESSION_LABELS[reason] ?? reason}</strong>
          <small>Open to see the affected stocks and full reason.</small>
        </span>
        <Badge variant="outline" className="summary-count">
          {unique.size} securit{unique.size === 1 ? "y" : "ies"} · {rows.length} row
          {rows.length === 1 ? "" : "s"}
        </Badge>
      </summary>
      <div className="suppressed-stock-grid">
        {[...stocks.entries()].map(([securityId, stockRows]) => {
          const firstRow = stockRows[0];
          const checks = filterChecks(firstRow);
          return (
            <article key={securityId} className="suppressed-stock-card">
              <div>
                <Link href={`/security/${securityId}`}>
                  {firstRow.symbol ?? securityId}
                </Link>
                <span>{stockRows.map((row) => `${row.horizon_days}d`).join(" + ")}</span>
              </div>
              {firstRow.composite !== null ? (
                <p className="stock-score">
                  Score {firstRow.composite.toFixed(1)} · Rank {firstRow.rank ?? "—"}
                </p>
              ) : null}
              <ul className="filter-checklist" aria-label="Filter results">
                {checks.map((check) => {
                  const display = FILTER_MARKS[check.state];
                  return (
                    <li key={check.label} className={`filter-check ${check.state}`}>
                      <span className="filter-mark" aria-hidden="true">{display.mark}</span>
                      <span className="filter-name">{check.label}</span>
                      <strong>{display.word}</strong>
                    </li>
                  );
                })}
              </ul>
              <details className="technical-reason">
                <summary>Why was it rejected?</summary>
                <p>{firstRow.detail}</p>
              </details>
            </article>
          );
        })}
      </div>
      <p className="rule-key">Rule key: {reason}</p>
    </details>
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
      <p className="page-lede">
        The bot&rsquo;s weekly picks. Zero is a valid result when no stock passes every rule.
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
            <div className="section-title-row">
              <div><p className="eyebrow">Latest decision</p><h2>This week</h2></div>
              <Badge variant="outline" className={candidates.rows.length ? "status-good" : "status-caution"}>
                {candidates.rows.length} candidate{candidates.rows.length === 1 ? "" : "s"}
              </Badge>
            </div>
            <div className="mini-facts">
              <span><small>Status</small><strong>{run.row.status}</strong></span>
              <span><small>Generated</small><strong>{formatEastern(run.row.started_at)}</strong></span>
              <span><small>Run</small><strong>run {run.row.run_id}</strong></span>
              {first ? <span><small>Cutoff</small><strong>evidence cutoff {first.data_cutoff_at}</strong></span> : null}
            </div>
            <p className="section-explainer">
              This week produced {candidates.rows.length} unique originating candidate{candidates.rows.length === 1 ? "" : "s"}
              {candidates.rows.length > 0 ? ` — not ${candidates.rows.length * books.rows.length} observations.` : "."}
            </p>

            {candidates.rows.length === 0 ? (
              <div className="friendly-empty caution-box">
                <strong>No stock passed every rule.</strong>
                <p>Open a rejection group below to see why; an empty selection is a result, not a gap.</p>
              </div>
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
            <div className="section-title-row">
              <div><p className="eyebrow">Why stocks were rejected</p><h2>Suppression log</h2></div>
              <span className="quiet-count">{new Set(suppressions.rows.map((row) => row.security_id)).size} stocks</span>
            </div>
            <p className="section-explainer">Reasons are grouped and closed by default. Open only what you want to inspect.</p>
            <div className="filter-legend" aria-label="Filter result legend">
              <span className="pass"><b>✓</b> Passed</span>
              <span className="fail"><b>✕</b> Failed</span>
              <span className="unknown"><b>?</b> Missing evidence / not checked</span>
            </div>
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
            <details className="simple-details">
              <summary><span><strong>Paper-trading books</strong><small>20-day and 60-day tracking</small></span></summary>
              <p className="section-explainer">
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
            </details>
          </section>
        </>
      )}
    </main>
  );
}
