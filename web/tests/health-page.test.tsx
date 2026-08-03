/**
 * The health page must render when the database is empty, and when it does not
 * exist at all. Those are the two states Phase 1 actually ships in.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const REPO_ROOT = path.resolve(__dirname, "..", "..");

let tempDir: string;

async function renderHealthPage(dbPath: string): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { default: HealthPage } = await import("@/app/health/page");
  const element = await HealthPage();
  return renderToStaticMarkup(element);
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-health-"));
});

afterEach(() => {
  vi.doUnmock("@/lib/paths");
  fs.rmSync(tempDir, { recursive: true, force: true });
});

describe("health page", () => {
  it("renders when the database file does not exist", async () => {
    const html = await renderHealthPage(path.join(tempDir, "missing.db"));
    expect(html).toContain("System health");
    expect(html).toContain("not created yet");
    expect(html).toContain("No data yet");
  });

  it("renders with an empty, migrated database", async () => {
    const dbPath = path.join(tempDir, "empty.db");
    const { default: Database } = await import("better-sqlite3");
    const db = new Database(dbPath);
    db.exec(
      fs.readFileSync(
        path.join(REPO_ROOT, "migrations", "001_operations_tables.up.sql"),
        "utf-8",
      ),
    );
    db.prepare(
      "INSERT INTO schema_migrations (version, applied_at) VALUES ('001', '2026-07-29T00:00:00Z')",
    ).run();
    db.close();

    const html = await renderHealthPage(dbPath);
    expect(html).toContain("System health");
    expect(html).toContain("connected");
    // Fixture, source health, pipeline runs and (since only migration 001 is
    // applied) the F12 verification section are all empty here.
    expect(html.match(/No data yet/g)?.length).toBe(4);
    // The applied migration is listed.
    expect(html).toContain("001");
  });

  it("shows the frozen config versions", async () => {
    const html = await renderHealthPage(path.join(tempDir, "missing.db"));
    const { REQUIRED_KEYS } = await import("@/lib/config");
    expect(html).toContain("Frozen configuration");
    expect(html).toContain(`${REQUIRED_KEYS.length} loaded`);
    expect(html).toContain("composite_threshold");
  });

  it("shows the Phase F verification report with real check results", async () => {
    const dbPath = path.join(tempDir, "verify.db");
    const { default: Database } = await import("better-sqlite3");
    const db = new Database(dbPath);
    for (const file of fs
      .readdirSync(path.join(REPO_ROOT, "migrations"))
      .filter((name) => name.endsWith(".up.sql"))
      .sort()) {
      db.exec(fs.readFileSync(path.join(REPO_ROOT, "migrations", file), "utf-8"));
    }
    db.prepare(
      "INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status, code_version) "
      + "VALUES ('verification-1', 'verification', '2026-08-05T00:00:00Z', "
      + "'2026-08-05T00:01:00Z', 'partial', 'v')",
    ).run();
    const insert = db.prepare(
      "INSERT INTO verification_results (run_id, check_number, check_name, status, detail, "
      + "evidence_json) VALUES ('verification-1', ?, ?, ?, ?, ?)",
    );
    insert.run(1, "Derived accounting metrics reproduce from stored facts", "pass",
      "748 of 748 rows reproduced", JSON.stringify({ rows_reproduced: 748 }));
    insert.run(5, "20 Form 4 filings hand-verified against EDGAR", "pending",
      "0 of 20 required filings verified against EDGAR (0 of 3 required amendments)",
      JSON.stringify({ rows: [] }));
    insert.run(9, "No metric displays zero where data is absent", "fail",
      "1 place where absent data may be rendering as zero",
      JSON.stringify({ violations: [{ table: "derived_fundamentals", metric: "roic" }] }));
    db.close();

    const html = await renderHealthPage(dbPath);
    expect(html).toContain("Phase F exit-criteria verification");
    expect(html).toContain("PASS");
    expect(html).toContain("PENDING");
    expect(html).toContain("FAIL");
    expect(html).toContain("748 of 748 rows reproduced");
    expect(html).toContain("Phase S may not begin until every check passes");
    // Evidence for each check is rendered, not just the one-line summary.
    expect(html).toContain("rows_reproduced");
    expect(html).toContain("roic");
  });
});
