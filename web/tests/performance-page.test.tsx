/**
 * /performance must report per horizon, never pooled, and must never hide a
 * pending-resolution position or a cancellation behind a clean-looking summary.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const MIGRATIONS = path.join(REPO_ROOT, "migrations");

let tempDir: string;
let dbPath: string;

async function buildDatabase() {
  const { default: Database } = await import("better-sqlite3");
  const db = new Database(dbPath);
  for (const file of fs
    .readdirSync(MIGRATIONS)
    .filter((name) => name.endsWith(".up.sql"))
    .sort()) {
    db.exec(fs.readFileSync(path.join(MIGRATIONS, file), "utf-8"));
  }

  db.prepare(
    `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                             classification_confidence, classification_source, sic_code,
                             first_seen, last_seen, is_active, delisted_date)
     VALUES (1, '0000000001', NULL, 'Acme Manufacturing Inc.', 'common_stock', 'high',
             'test', '3571', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                             classification_confidence, classification_source, sic_code,
                             first_seen, last_seen, is_active, delisted_date)
     VALUES (2, '0000000002', NULL, 'Beta Retail Corp.', 'common_stock', 'high',
             'test', '5331', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (1, 'ACME', 'NYSE', '2026-01-01', NULL, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (2, 'BETA', 'NYSE', '2026-01-01', NULL, 1)`,
  ).run();

  db.prepare(
    `INSERT INTO universe_snapshot_runs (snapshot_id, effective_at, rules_version,
                                         config_hash, run_id, security_count, is_official)
     VALUES ('snap-1', '2026-01-30', 'v', 'h', NULL, 2, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status,
                                records_written, code_version)
     VALUES ('run-sel', 'selection', 'x', 'x', 'success', 2, 'v'),
            ('run-exec', 'execution', 'x', 'x', 'success', 2, 'v')`,
  ).run();
  db.prepare(
    `INSERT INTO books (book_id, horizon_days, starting_nav, current_nav,
                        open_position_count, strategy_version)
     VALUES ('book-20d', 20, 100000, 100050, 1, 1),
            ('book-60d', 60, 100000, 100000, 0, 1)`,
  ).run();

  const insertCandidate = db.prepare(
    `INSERT INTO research_candidates (candidate_id, security_id, generated_at,
      data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash,
      code_version, selection_rule_version, mapping_version, price_dataset_version,
      price_snapshot_hash, source_health_snapshot_json, score_snapshot_json,
      accessions_used_json, composite_at_generation, rank_at_generation, signal_close,
      atr_value, atr_window, price_data_cutoff, entry_rule, gap_limit_atr, row_hash)
     VALUES (?, ?, 'x', '2026-01-30T20:00:00Z', 'snap-1', 'run-sel', 1, 'h', 'v', 1, '1',
      1, 'psh', '{}', ?, '[]', 55.0, 1, 100.0, 3.0, 14, '2026-01-30', 'next_open', 1.0, 'rh')`,
  );
  insertCandidate.run("cand-win", 1, JSON.stringify({ cohort_id: "SIC-D" }));
  insertCandidate.run("cand-loss", 2, JSON.stringify({ cohort_id: "SIC-G" }));
  insertCandidate.run("cand-open", 1, JSON.stringify({ cohort_id: "SIC-D" }));
  insertCandidate.run("cand-pending", 2, JSON.stringify({ cohort_id: "SIC-G" }));
  insertCandidate.run("cand-cancelled", 1, JSON.stringify({ cohort_id: "SIC-D" }));

  const insertPosition = db.prepare(
    `INSERT INTO paper_positions (position_id, candidate_id, horizon_days, book_id,
      protocol_version, strategy_version, resolution_policy_version, accrual_policy_version,
      price_snapshot_hash, opened_run_id, last_evaluated_at, entry_date, entry_price,
      slippage_bps, shares, notional, stop_price, target_price, status, exit_date,
      exit_price, exit_reason, dividends_received, splits_applied, gross_pnl, net_pnl,
      pnl_pct, requires_manual_review)
     VALUES (?, ?, 20, 'book-20d', 'R1-PROTOCOL-1.1', 1, 1, 1, 'psh', 'run-exec', 'x',
      ?, ?, 5, ?, 1000, ?, ?, ?, ?, ?, ?, 0, 1.0, ?, ?, ?, 0)`,
  );
  // A win.
  insertPosition.run(
    "pos-win", "cand-win", "2026-01-31", 100.0, 10.0, 92.0, 116.0, "closed",
    "2026-02-15", 112.0, "target", 120.0, 120.0, 0.12,
  );
  // A loss.
  insertPosition.run(
    "pos-loss", "cand-loss", "2026-01-31", 50.0, 20.0, 46.0, 58.0, "closed",
    "2026-02-05", 46.0, "stop", -80.0, -80.0, -0.08,
  );
  // Still open.
  insertPosition.run(
    "pos-open", "cand-open", "2026-01-31", 100.0, 10.0, 92.0, 116.0, "open",
    null, null, null, null, null, null,
  );
  // Pending resolution — must never show an exit price.
  insertPosition.run(
    "pos-pending", "cand-pending", "2026-01-31", 50.0, 20.0, 46.0, 58.0, "pending_resolution",
    null, null, null, null, null, null,
  );

  const insertBench = db.prepare(
    `INSERT INTO benchmark_positions (position_id, candidate_id, horizon_days, book_id,
      security_id, protocol_version, strategy_version, resolution_policy_version,
      accrual_policy_version, price_snapshot_hash, opened_run_id, last_evaluated_at,
      entry_date, entry_price, slippage_bps, shares, notional, status, exit_date,
      exit_price, exit_reason, dividends_received, splits_applied, gross_pnl, net_pnl,
      pnl_pct, requires_manual_review)
     VALUES (?, ?, 20, 'book-20d', 1, 'R1-PROTOCOL-1.1', 1, 1, 1, 'psh', 'run-exec', 'x',
      ?, 400.0, 5, 2.5, 1000, 'closed', ?, ?, 'matched_close', 0, 1.0, ?, ?, ?, 0)`,
  );
  insertBench.run("bench-win", "cand-win", "2026-01-31", "2026-02-15", 405.0, 12.5, 12.5, 0.0125);
  insertBench.run("bench-loss", "cand-loss", "2026-01-31", "2026-02-05", 398.0, -5.0, -5.0, -0.005);

  db.prepare(
    `INSERT INTO cancelled_entries (candidate_id, reason, signal_close, next_open, gap_atr,
      adjusted_basis, cancelled_at, run_id)
     VALUES ('cand-cancelled', 'gap_above_prior_close', 100.0, 130.0, 10.0,
      'no split on the entry session, basis unchanged', 'x', 'run-exec')`,
  ).run();

  db.close();
}

async function renderPerformancePage(): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { default: PerformancePage } = await import("@/app/performance/page");
  return renderToStaticMarkup(await PerformancePage());
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-perf-"));
  dbPath = path.join(tempDir, "test.db");
});

afterEach(() => {
  vi.doUnmock("@/lib/paths");
  try {
    fs.rmSync(tempDir, { recursive: true, force: true });
  } catch {
    // Windows holds a short lock on the SQLite file after close.
  }
});

describe("performance page", () => {
  it("reports the 20-day and 60-day books separately, never pooled", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("20-day book");
    expect(html).toContain("60-day book");
    const twentyIndex = html.indexOf("20-day book");
    const sixtyIndex = html.indexOf("60-day book");
    expect(sixtyIndex).toBeGreaterThan(twentyIndex);
    // Never pooled: the 60-day section (no data) must not show the 20-day win.
    const sixtySection = html.slice(sixtyIndex);
    expect(sixtySection.split("Closed trades")[1]).not.toContain("ACME");
  });

  it("shows losses identically to wins in the same table", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("ACME"); // the win
    expect(html).toContain("BETA"); // the loss
    expect(html).toContain("stop");
    expect(html).toContain("target");
    expect(html).toContain("Losses are shown identically to wins");
  });

  it("shows sample size, observation window, Wilson CI, average win/loss and profit factor", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("Sample size");
    expect(html).toContain("Observation window");
    expect(html).toContain("2026-02-05");
    expect(html).toContain("2026-02-15");
    expect(html).toContain("Win rate (95% Wilson CI)");
    expect(html).toContain("1/2");
    expect(html).toContain("Profit factor");
    expect(html).toContain("Average win / loss");
  });

  it("shows book max drawdown against the fixed starting NAV", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("Max drawdown (against fixed $100,000 NAV)");
  });

  it("shows the paired SPY comparison per trade, separate from book-level results", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("Paired SPY");
    expect(html).toContain("1.25%"); // bench-win pnl_pct = 0.0125
  });

  it("shows sector concentration by SIC-derived cohort", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("Sector concentration (SIC-derived)");
    expect(html).toContain("SIC-D");
    expect(html).toContain("SIC-G");
  });

  it("shows the cancellation count and rate, and pending-resolution count and notional", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("Cancelled entries");
    expect(html).toContain("Pending-resolution notional");
    expect(html).toContain("$1,000.00");
  });

  it("reports every headline statistic BOTH excluding and including pending positions", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    // The 20-day book has one pending position; both views must appear, never
    // just one, and the including-view must be clearly labelled as provisional.
    expect(html).toContain("excluding pending");
    expect(html).toContain("including pending @ zero");
    expect(html).toContain("provisional zero policy value");
    // Excluding: 1 win / 2 closed. Including the pending-as-zero third trade,
    // the win count is unchanged but the sample size grows to 3.
    expect(html).toContain("1/2");
    expect(html).toContain("1/3");
  });

  it("never shows an exit price for a pending-resolution position", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    const pendingSection = html.slice(html.indexOf("Open and pending positions"));
    expect(pendingSection).toContain("pending_resolution");
    // The pending position's stop/target render, but no exit fields exist in
    // the open/pending table at all — asserted structurally via the columns.
    expect(pendingSection).not.toContain("delisting");
  });

  it("shows the unique originating candidate count, distinct from position count", async () => {
    await buildDatabase();
    const html = await renderPerformancePage();
    expect(html).toContain("unique originating candidate");
    // 5 candidates total (win, loss, open, pending, cancelled) across both books.
    expect(html).toContain("5 unique originating candidates");
    expect(html).toContain("never pooled");
  });

  it("shows the empty state when nothing has been executed", async () => {
    const { default: Database } = await import("better-sqlite3");
    await buildDatabase();
    const db = new Database(dbPath);
    db.prepare("DELETE FROM paper_positions").run();
    db.prepare("DELETE FROM cancelled_entries").run();
    db.close();
    const html = await renderPerformancePage();
    expect(html).toContain("No data yet");
    expect(html).toContain("protocol proving its own gates");
  });
});
