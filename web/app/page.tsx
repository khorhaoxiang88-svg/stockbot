import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  SUPPRESSION_LABELS,
  getActiveExperiment,
  getBooks,
  getCandidatesForRun,
  getLatestSelectionRun,
  getLatestScannerActivity,
  getLatestUniverseSnapshotRun,
  getOfficialPaperPositions,
  getPublishedSelectionRun,
  getRecentRuns,
  getSuppressionsForRun,
  getTopRankedScores,
  getUniverseSnapshotRows,
} from "@/lib/db";
import { formatEastern } from "@/lib/time";

export const dynamic = "force-dynamic";

function score(value: number | null) {
  return value === null ? "—" : value.toFixed(1);
}

export default function Home() {
  const { row: experiment } = getActiveExperiment();
  const monthly = getLatestUniverseSnapshotRun("monthly_membership");
  const universe = monthly.row ? getUniverseSnapshotRows(monthly.row.snapshot_id).rows : [];
  const included = universe.filter((row) => row.status === "included").length;
  const ranked = getTopRankedScores(8).rows;
  const publishedRun = getPublishedSelectionRun();
  const latestRun = getLatestSelectionRun();
  const candidates = publishedRun.row ? getCandidatesForRun(publishedRun.row.run_id).rows : [];
  const selectedIds = new Set(candidates.map((candidate) => candidate.security_id));
  const suppressions = publishedRun.row ? getSuppressionsForRun(publishedRun.row.run_id).rows : [];
  const suppressedStockCount = new Set(suppressions.map((row) => row.security_id)).size;
  const suppressionCounts = suppressions.reduce<Record<string, number>>((counts, row) => {
    counts[row.suppression_reason] = (counts[row.suppression_reason] ?? 0) + 1;
    return counts;
  }, {});
  const leadingSuppression = Object.entries(suppressionCounts).sort((a, b) => b[1] - a[1])[0]?.[0];
  const recentRuns = getRecentRuns(8).rows;
  const scannerActivity = getLatestScannerActivity(12).rows;
  const activeRuns = recentRuns.filter((run) => run.status === "running" && !run.finished_at);
  const scannerActive = activeRuns.length > 0 || scannerActivity.some((row) => row.run_status === "running");
  const latestScannerItem = scannerActivity[0] ?? null;
  const books = getBooks().rows;
  const officialPositions = books.reduce(
    (sum, book) => sum + getOfficialPaperPositions(book.horizon_days).rows.length,
    0,
  );
  const dataAvailable = ranked.length > 0 || universe.length > 0 || Boolean(experiment);

  return (
    <main className="dashboard-shell">
      <section className="dashboard-hero">
        <div>
          <p className="dashboard-kicker">Live stock research</p>
          <h1>Your stockbot, at a glance.</h1>
          <p className="dashboard-lede">
            Picks, live scanner activity and ranked stocks. Open any box for the evidence.
          </p>
          <div className="dashboard-actions">
            <Link href="/candidates" className="action-primary">Picked stocks</Link>
            <Link href="/universe" className="action-secondary">All stocks</Link>
          </div>
        </div>
        <aside className="experiment-card">
          <span>Experiment status</span>
          <strong>{experiment ? "LIVE" : "NOT LAUNCHED"}</strong>
          <dl>
            <div><dt>Experiment</dt><dd>{experiment?.experiment_id ?? "—"}</dd></div>
            <div><dt>Strategy</dt><dd>{experiment ? `v${experiment.strategy_version}` : "—"}</dd></div>
            <div><dt>Started</dt><dd>{experiment ? formatEastern(experiment.started_at) : "—"}</dd></div>
            <div><dt>Official positions</dt><dd>{officialPositions}</dd></div>
          </dl>
        </aside>
      </section>

      {!dataAvailable ? (
        <section className="db-empty-state">
          <strong>Website ready; local research database not found.</strong>
          <p>
            This checkout does not include <code>data/stockbot.db</code> because the
            database is intentionally excluded from Git. Run the website beside the
            bot&rsquo;s real database to populate every dashboard automatically.
          </p>
          <Link href="/health">Open system health →</Link>
        </section>
      ) : null}

      <section className="dashboard-metrics" aria-label="System summary">
        <article><span>Universe evaluated</span><strong>{universe.length || "—"}</strong><small>{included ? `${included} included` : "latest monthly snapshot"}</small></article>
        <article><span>Latest ranked set</span><strong>{ranked.length || "—"}</strong><small>{ranked[0]?.score_date ?? "awaiting database"}</small></article>
        <article><span>Published candidates</span><strong>{candidates.length}</strong><small>{publishedRun.row ? formatEastern(publishedRun.row.started_at) : "no completed selection"}</small></article>
        <article><span>Latest pipeline run</span><strong>{latestRun.row?.status ?? "—"}</strong><small>{latestRun.row ? formatEastern(latestRun.row.started_at) : "selection has not run"}</small></article>
      </section>

      <section className="operations-grid" aria-label="Stock selection and scanner activity">
        <article className="operations-panel picked-panel">
          <div className="operations-heading">
            <div>
              <p className="dashboard-kicker">Latest weekly decision</p>
              <h2>Picked stocks</h2>
            </div>
            <span className={`operations-status ${candidates.length ? "is-live" : "is-idle"}`}>
              {candidates.length} picked
            </span>
          </div>
          {candidates.length ? (
            <div className="picked-list">
              {candidates.map((candidate) => (
                <Link href={`/security/${candidate.security_id}`} key={candidate.candidate_id}>
                  <span>#{candidate.rank_at_generation}</span>
                  <strong>{candidate.symbol ?? candidate.security_id}</strong>
                  <small>{candidate.name ?? "Research candidate"}</small>
                  <b>{candidate.composite_at_generation.toFixed(1)}</b>
                </Link>
              ))}
            </div>
          ) : (
            <div className="operations-empty">
              <strong>No stocks were picked in the latest published selection.</strong>
              <p>
                That is a real bot result, not missing website data.
                {suppressedStockCount && leadingSuppression
                  ? ` ${suppressedStockCount} stocks were considered. Main rejection: ${SUPPRESSION_LABELS[leadingSuppression] ?? leadingSuppression}.`
                  : " Every stock that was considered is preserved with its rejection reason."}
              </p>
            </div>
          )}
          <div className="operations-foot">
            <span>
              {publishedRun.row
                ? `Run ${publishedRun.row.status} · ${formatEastern(publishedRun.row.started_at)}`
                : "No published selection run"}
            </span>
            <Link href="/candidates">Open picks and rejection log →</Link>
          </div>
        </article>

        <article className="operations-panel scanner-panel">
          <div className="operations-heading">
            <div>
              <p className="dashboard-kicker">Pipeline activity</p>
              <h2>Stocks being scanned</h2>
            </div>
            <span className={`operations-status ${scannerActive ? "is-live" : "is-idle"}`}>
              {scannerActive ? "Scanning now" : "Scanner idle"}
            </span>
          </div>
          {scannerActivity.length ? (
            <>
              <p className="scanner-note">
                {scannerActive
                  ? "The scanner is running. These are the latest securities it has finished processing."
                  : `No scan is running right now. Latest recorded activity was ${formatEastern(latestScannerItem!.attempted_at)}.`}
              </p>
              <div className="scanner-tape" aria-label="Latest scanned stocks">
                {scannerActivity.map((item) => (
                  <span className={item.status === "failed" ? "scan-failed" : ""} key={`${item.batch_id}-${item.stage}-${item.item_key}`}>
                    <strong>{item.item_key}</strong>
                    <small>{item.stage} · {item.status}</small>
                  </span>
                ))}
              </div>
            </>
          ) : (
            <div className="operations-empty">
              <strong>No scanner activity has been recorded yet.</strong>
              <p>The list fills automatically as the scaled ingestion pipeline finishes each stock.</p>
            </div>
          )}
          <div className="operations-foot">
            <span>{activeRuns.length ? `${activeRuns.length} active pipeline run${activeRuns.length === 1 ? "" : "s"}` : "Waiting for the next scheduled run"}</span>
            <Link href="/runs" className="dashboard-link-button">Open complete run history →</Link>
          </div>
        </article>
      </section>

      <section className="dashboard-section-head">
        <div>
          <p className="dashboard-kicker">Latest deterministic ranking</p>
          <h2>Top ranked stocks</h2>
          <p className="section-explainer">High score does not mean selected. Open a card for its chart and short company summary.</p>
        </div>
        <Link href="/rankings" className="dashboard-link-button">View all ranked stocks →</Link>
      </section>

      {ranked.length ? (
        <section className="rank-grid">
          {ranked.map((row) => (
            <Link href={`/security/${row.security_id}`} className="rank-card" key={row.security_id}>
              <div className="rank-card-head">
                <span className="rank-number">#{row.rank}</span>
                <div><strong>{row.symbol ?? row.security_id}</strong><small>{row.name}</small></div>
                <b>{row.composite_score.toFixed(1)}</b>
              </div>
              <div className="score-track"><i style={{ width: `${Math.max(0, Math.min(100, row.composite_score))}%` }} /></div>
              <div className="rank-components">
                <span><small>Value</small>{score(row.value_score)}</span>
                <span><small>Quality</small>{score(row.quality_score)}</span>
                <span><small>Momentum</small>{score(row.momentum_score)}</span>
                <span><small>Dilution</small>{row.dilution_penalty.toFixed(1)}</span>
              </div>
              <p>
                {selectedIds.has(row.security_id)
                  ? <strong className="selected-label">✓ Selected</strong>
                  : "Ranked, not selected"}
                <span className="open-stock-label">Open chart →</span>
              </p>
            </Link>
          ))}
        </section>
      ) : (
        <section className="rank-empty">
          No score rows are available in this checkout. The cards appear automatically
          when the website can read the bot&rsquo;s database.
        </section>
      )}

      <details className="dashboard-more">
        <summary>How the bot works</summary>
        <section className="workflow-grid">
          <article><span>01</span><h3>Universe</h3><p>Checks which stocks have enough reliable data.</p><Link href="/universe">Open stocks →</Link></article>
          <article><span>02</span><h3>Scores</h3><p>Scores value, quality, momentum, insiders and dilution.</p><Link href={ranked[0] ? `/security/${ranked[0].security_id}` : "/universe"}>Inspect a score →</Link></article>
          <article><span>03</span><h3>Picks</h3><p>Selects only stocks that pass every weekly rule.</p><Link href="/candidates">See picks →</Link></article>
          <article><span>04</span><h3>Results</h3><p>Tracks separate 20-day and 60-day paper books.</p><Link href="/performance">View results →</Link></article>
        </section>
      </details>

      <details className="dashboard-more">
        <summary>Research safeguards</summary>
        <section className="transparency-panel">
          <div><h2>Every “no” stays visible.</h2></div>
          <div className="transparency-list">
            <p><Badge variant="outline">Missing ≠ zero</Badge> Missing evidence never becomes a fake score.</p>
            <p><Badge variant="outline">Append-only</Badge> Published results cannot be quietly rewritten.</p>
            <p><Badge variant="outline">Zero is valid</Badge> No picks is a complete result.</p>
          </div>
        </section>
      </details>
    </main>
  );
}
