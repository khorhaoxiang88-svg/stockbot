/**
 * /candidates must show this week's selections with full provenance AND the
 * suppression log accounting for everything that was not selected. An empty
 * selection is a result, not an empty page.
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

const HEALTH = JSON.stringify({
  checked_at_cutoff: "2026-07-24T20:00:00Z",
  sources: [
    { source: "prices_daily:price_ingest", ok: true, detail: "last succeeded 2026-07-24T21:05:00Z, 1h ago, SLA 24h" },
    { source: "filings:insider", ok: false, detail: "last succeeded 2026-07-01T00:00:00Z, 560h ago against a 48h SLA" },
  ],
});

const SCORE = JSON.stringify({
  composite_score: 58.0785,
  rank: 1,
  cohort_id: "SIC-D",
  threshold_applied: 40,
  threshold_is_provisional: true,
});

async function buildDatabase(withCandidate: boolean) {
  const { default: Database } = await import("better-sqlite3");
  const db = new Database(dbPath);
  for (const file of fs
    .readdirSync(MIGRATIONS)
    .filter((name) => name.endsWith(".up.sql"))
    .sort()) {
    db.exec(fs.readFileSync(path.join(MIGRATIONS, file), "utf-8"));
  }

  const insertSecurity = db.prepare(
    `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                             classification_confidence, classification_source, sic_code,
                             first_seen, last_seen, is_active, delisted_date)
     VALUES (?, ?, NULL, ?, 'common_stock', 'high', 'test', '3571',
             '2026-07-24T00:00:00Z', '2026-07-24T00:00:00Z', 1, NULL)`,
  );
  insertSecurity.run(1, "0000000001", "Acme Manufacturing Inc.");
  insertSecurity.run(2, "0000000002", "Beta Industrial Corp.");
  const insertListing = db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (?, ?, 'NYSE', '2026-07-24', NULL, 1)`,
  );
  insertListing.run(1, "ACME");
  insertListing.run(2, "BETA");

  db.prepare(
    `INSERT INTO universe_snapshot_runs (snapshot_id, effective_at, rules_version,
                                         config_hash, run_id, security_count, is_official)
     VALUES ('universe-2026-07-24-abcdef12', '2026-07-24', 'F8-scoring/v1', 'cfg', NULL, 2, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status,
                                records_written, code_version)
     VALUES ('selection-testrun01', 'selection', '2026-07-24T21:00:00Z',
             '2026-07-24T21:00:05Z', 'success', ?, 'selection-rule-1.1/v1')`,
  ).run(withCandidate ? 1 : 0);

  db.prepare(
    `INSERT INTO books (book_id, horizon_days, starting_nav, current_nav,
                        open_position_count, strategy_version)
     VALUES ('book-20d', 20, 100000, 100000, 0, 1), ('book-60d', 60, 100000, 100000, 0, 1)`,
  ).run();

  if (withCandidate) {
    db.prepare(
      `INSERT INTO research_candidates (candidate_id, security_id, generated_at,
        data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash,
        code_version, selection_rule_version, mapping_version, price_dataset_version,
        price_snapshot_hash, source_health_snapshot_json, score_snapshot_json,
        accessions_used_json, composite_at_generation, rank_at_generation, signal_close,
        atr_value, atr_window, price_data_cutoff, entry_rule, gap_limit_atr, row_hash)
       VALUES ('cand-abc123', 1, '2026-07-24T21:00:00Z', '2026-07-24T20:00:00Z',
        'universe-2026-07-24-abcdef12', 'selection-testrun01', 1,
        'd22cc26e7e1955e7a25c48bf81046ddd', 'selection-rule-1.1/v1+provisional', 1, '1',
        2, 'ps-hash', ?, ?, '["0000320193-25-000079","0000320193-24-000123"]',
        58.0785, 1, 214.35, 4.212, 14, '2026-07-24',
        'next_regular_session_open, subject to the gap filter', 1.0, 'rowhash0123456789')`,
    ).run(HEALTH, SCORE);
  }

  const insertSuppression = db.prepare(
    `INSERT INTO suppressed_signals (run_id, security_id, horizon_days, composite,
                                     "rank", suppression_reason, detail)
     VALUES ('selection-testrun01', ?, ?, ?, ?, ?, ?)`,
  );
  insertSuppression.run(2, 20, 44.1, 6, "cohort_cap",
    "cohort SIC-D already has 2 candidates, the configured maximum from one SIC-derived cohort");
  insertSuppression.run(2, 60, 44.1, 6, "cohort_cap",
    "cohort SIC-D already has 2 candidates, the configured maximum from one SIC-derived cohort");
  insertSuppression.run(1, 20, 58.0785, 1, "open_position",
    "a position is already open for this security at the 20-day horizon; the qualifying signal is logged rather than discarded");

  db.close();
}

/**
 * A required source failing must not blank the screener: the last published
 * (real) selection stays on screen, tagged stale, while the newer blocked
 * run -- every row suppressed as stale_source, zero candidates -- is named
 * in a warning rather than displayed as this week's (empty) result.
 */
async function buildStaleBlockedDatabase() {
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
             'test', '3571', '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (1, 'ACME', 'NYSE', '2026-07-17', NULL, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO universe_snapshot_runs (snapshot_id, effective_at, rules_version,
                                         config_hash, run_id, security_count, is_official)
     VALUES ('universe-2026-07-17-abcdef12', '2026-07-17', 'F8-scoring/v1', 'cfg', NULL, 1, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO books (book_id, horizon_days, starting_nav, current_nav,
                        open_position_count, strategy_version)
     VALUES ('book-20d', 20, 100000, 100000, 0, 1), ('book-60d', 60, 100000, 100000, 0, 1)`,
  ).run();

  // The published run, from last week: one real candidate.
  db.prepare(
    `INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status,
                                records_written, code_version)
     VALUES ('selection-published01', 'selection', '2026-07-17T21:00:00Z',
             '2026-07-17T21:00:05Z', 'success', 1, 'selection-rule-1.1/v1')`,
  ).run();
  db.prepare(
    `INSERT INTO research_candidates (candidate_id, security_id, generated_at,
      data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash,
      code_version, selection_rule_version, mapping_version, price_dataset_version,
      price_snapshot_hash, source_health_snapshot_json, score_snapshot_json,
      accessions_used_json, composite_at_generation, rank_at_generation, signal_close,
      atr_value, atr_window, price_data_cutoff, entry_rule, gap_limit_atr, row_hash)
     VALUES ('cand-published01', 1, '2026-07-17T21:00:00Z', '2026-07-17T20:00:00Z',
      'universe-2026-07-17-abcdef12', 'selection-published01', 1,
      'd22cc26e7e1955e7a25c48bf81046ddd', 'selection-rule-1.1/v1', 1, '1',
      2, 'ps-hash', ?, ?, '["0000320193-25-000079"]',
      58.0785, 1, 214.35, 4.212, 14, '2026-07-17',
      'next_regular_session_open, subject to the gap filter', 1.0, 'rowhash0123456789')`,
  ).run(HEALTH, SCORE);

  // This week's run: blocked entirely by a stale required source.
  db.prepare(
    `INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status,
                                records_written, code_version)
     VALUES ('selection-blocked01', 'selection', '2026-07-24T21:00:00Z',
             '2026-07-24T21:00:05Z', 'partial', 0, 'selection-rule-1.1/v1')`,
  ).run();
  db.prepare(
    `INSERT INTO suppressed_signals (run_id, security_id, horizon_days, composite,
                                     "rank", suppression_reason, detail)
     VALUES ('selection-blocked01', 1, 20, NULL, NULL, 'stale_source',
             'prices_daily:price_ingest: last succeeded 2026-07-20T00:00:00Z, 117h ago against a 24h SLA')`,
  ).run();

  db.close();
}

async function renderCandidatesPage(): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { default: CandidatesPage } = await import("@/app/candidates/page");
  return renderToStaticMarkup(await CandidatesPage());
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-cand-"));
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

describe("candidates page", () => {
  it("shows the selection with its full provenance", async () => {
    await buildDatabase(true);
    const html = await renderCandidatesPage();
    expect(html).toContain("Research candidates");
    expect(html).toContain("1 candidate");
    expect(html).toContain("ACME");
    expect(html).toContain("58.0785");
    expect(html).toContain("214.35");
    expect(html).toContain("4.212");
    // Provenance: every stamp that pins the decision.
    expect(html).toContain("strategy v1");
    expect(html).toContain("rule v1");
    expect(html).toContain("d22cc26e7e19");
    expect(html).toContain("universe-2026-07-24-abcdef12");
    expect(html).toContain("2 accessions used");
    expect(html).toContain("row hash rowhash01234");
    expect(html).toContain("evidence cutoff 2026-07-24T20:00:00Z");
  });

  it("states the unique originating candidate count, not the position count", async () => {
    await buildDatabase(true);
    const html = await renderCandidatesPage();
    expect(html).toContain("unique originating");
    expect(html).toContain("not two independent observations");
    expect(html).toContain("never pool them");
  });

  it("shows the suppression log grouped by reason with the detail", async () => {
    await buildDatabase(true);
    const html = await renderCandidatesPage();
    expect(html).toContain("Suppression log");
    expect(html).toContain("Cohort already at its maximum");
    expect(html).toContain("A position is already open at this horizon");
    expect(html).toContain("logged rather than discarded");
    expect(html).toContain("BETA");
    // Grouped counts distinguish securities from per-book rows.
    expect(html).toContain("1 security · 2 rows");
  });

  it("renders the freshness snapshot stored with the candidate", async () => {
    await buildDatabase(true);
    const html = await renderCandidatesPage();
    expect(html).toContain("Source freshness at the cutoff");
    expect(html).toContain("prices_daily:price_ingest");
    expect(html).toContain("560h ago against a 48h SLA");
  });

  it("shows both books with their starting NAV and the sizing caveat", async () => {
    await buildDatabase(true);
    const html = await renderCandidatesPage();
    expect(html).toContain("book-20d");
    expect(html).toContain("book-60d");
    expect(html).toContain("100,000.00");
    expect(html).toContain("not");
    expect(html).toContain("recommended position sizing");
  });

  it("treats an empty selection as a result, not an empty page", async () => {
    await buildDatabase(false);
    const html = await renderCandidatesPage();
    expect(html).toContain("0 candidates");
    expect(html).toContain("an empty selection is a result, not a gap");
    // The suppression log still explains what happened.
    expect(html).toContain("Suppression log");
    expect(html).toContain("Cohort already at its maximum");
  });

  it("preserves the last published screener when a required source blocks the newest run", async () => {
    await buildStaleBlockedDatabase();
    const html = await renderCandidatesPage();
    // The old, real candidate is still shown, not the newer empty run.
    expect(html).toContain("1 candidate");
    expect(html).toContain("ACME");
    expect(html).toContain("run selection-published01");
    // A prominent warning names the block and the stale reason.
    expect(html).toContain("Screener stale");
    expect(html).toContain("blocked by a required source failure");
    expect(html).toContain("prices_daily:price_ingest");
    expect(html).toContain("117h ago against a 24h SLA");
  });
});
