/**
 * /debug/[security_id] must render every stored row for a security, unformatted,
 * across every table -- so state can be inspected without reading code.
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
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (1, 'ACME', 'NYSE', '2026-01-01', NULL, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO fixture_manifest (security_id, symbol_at_selection, inclusion_reason,
                                   category, added_at, manifest_version)
     VALUES (1, 'ACME', 'test', 'ordinary', '2026-01-01T00:00:00Z', '1')`,
  ).run();
  db.prepare(
    `INSERT INTO price_dataset_versions (dataset_version, created_at, provider, reason)
     VALUES (1, '2026-01-01T00:00:00Z', 'test', 'seed')`,
  ).run();
  db.prepare(
    `INSERT INTO prices (security_id, date, open, high, low, close, volume, provider,
                         first_seen_at, last_verified_at, revision, price_data_version)
     VALUES (1, '2026-01-01', 100, 101, 99, 100, 1000000, 'test', 'x', 'x', 0, 1)`,
  ).run();

  db.prepare(
    `INSERT INTO risk_flags (security_id, as_of_date, flag_code, severity, evidence_text,
                             source_accession, is_unknown)
     VALUES (1, '2026-01-01', 'high_leverage', 'none', 'Not detected.', 'none', 0)`,
  ).run();

  db.close();
}

async function renderDebugPage(id: string): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { default: DebugPage } = await import("@/app/debug/[security_id]/page");
  const element = await DebugPage({ params: Promise.resolve({ security_id: id }) });
  return renderToStaticMarkup(element);
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-debug-"));
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

describe("debug page", () => {
  it("renders raw rows for tables that have data", async () => {
    await buildDatabase();
    const html = await renderDebugPage("1");
    expect(html).toContain("Debug: security 1");
    expect(html).toContain("Identity");
    expect(html).toContain("securities");
    expect(html).toContain("0000000001");
    expect(html).toContain("Raw prices");
    expect(html).toContain("Risk flags");
    expect(html).toContain("high_leverage");
  });

  it("shows NULL explicitly rather than an empty cell", async () => {
    await buildDatabase();
    const html = await renderDebugPage("1");
    // securities.delisted_date is NULL for this fixture row.
    expect(html).toContain("NULL");
  });

  it("lists empty tables separately, collapsed, rather than omitting them", async () => {
    await buildDatabase();
    const html = await renderDebugPage("1");
    expect(html).toContain("table(s) empty for this security");
    expect(html).toContain("table(s) with zero rows for this security");
  });

  it("shows the true row count and does not silently truncate", async () => {
    await buildDatabase();
    const html = await renderDebugPage("1");
    expect(html).toContain("1 of 1 row");
  });

  it("links back to the formatted security page", async () => {
    await buildDatabase();
    const html = await renderDebugPage("1");
    expect(html).toContain('href="/security/1"');
    expect(html).toContain("Go to the formatted page");
  });

  it("renders for a security with no CIK without erroring", async () => {
    await buildDatabase();
    const { default: Database } = await import("better-sqlite3");
    const db = new Database(dbPath);
    db.prepare(
      `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                               classification_confidence, classification_source, sic_code,
                               first_seen, last_seen, is_active, delisted_date)
       VALUES (2, NULL, NULL, 'Some Warrant', 'warrant', 'high', 'test', NULL,
               '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, NULL)`,
    ).run();
    db.close();
    const html = await renderDebugPage("2");
    expect(html).toContain("CIK none");
    expect(html).not.toContain("Error");
  });

  it("shows an empty state for a security with no rows anywhere", async () => {
    await buildDatabase();
    const { default: Database } = await import("better-sqlite3");
    const db = new Database(dbPath);
    db.prepare(
      `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                               classification_confidence, classification_source, sic_code,
                               first_seen, last_seen, is_active, delisted_date)
       VALUES (3, '0000000003', NULL, 'Untouched Inc.', 'common_stock', 'high', 'test',
               '3571', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, NULL)`,
    ).run();
    db.close();
    const html = await renderDebugPage("3");
    // securities itself always has one row (identity), so this is not fully
    // empty, but the OTHER tables should report zero rather than erroring.
    expect(html).toContain("Debug: security 3");
    expect(html).not.toContain("Error");
  });

  it("reports a missing database rather than crashing", async () => {
    const html = await renderDebugPage("1"); // dbPath never created
    expect(html).toContain("No data yet");
  });
});
