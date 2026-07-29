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
    // Fixture, source health and pipeline runs are all empty here.
    expect(html.match(/No data yet/g)?.length).toBe(3);
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
});
