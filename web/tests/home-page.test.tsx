/**
 * The homepage status line is derived from the database (getActiveExperiment),
 * not hand-written "current phase" prose -- that went stale for months (still
 * said "Phase 1: project skeleton... No market data yet" through S1-O3, never
 * touched when a phase shipped because nothing forced it to be). These tests
 * guard against that regressing back to static text.
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

async function migrateOnly() {
  const { default: Database } = await import("better-sqlite3");
  const db = new Database(dbPath);
  for (const file of fs
    .readdirSync(MIGRATIONS)
    .filter((name) => name.endsWith(".up.sql"))
    .sort()) {
    db.exec(fs.readFileSync(path.join(MIGRATIONS, file), "utf-8"));
  }
  return db;
}

async function renderHomePage(): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { default: Home } = await import("@/app/page");
  return renderToStaticMarkup(await Home());
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-home-"));
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

describe("home page", () => {
  it("never shows the stale hardcoded Phase 1 text", async () => {
    const db = await migrateOnly();
    db.close();
    const html = await renderHomePage();
    expect(html).not.toContain("Phase 1");
    expect(html).not.toContain("No market data yet");
  });

  it("states no official experiment has launched when none has", async () => {
    const db = await migrateOnly();
    db.close();
    const html = await renderHomePage();
    expect(html).toContain("No official experiment has launched yet");
  });

  it("states the live experiment's start date and strategy version when one is active", async () => {
    const db = await migrateOnly();
    db.prepare(
      `INSERT INTO experiments (experiment_id, strategy_version, selection_rule_version,
        protocol_version, config_hash, started_at, status)
       VALUES ('exp-test', 2, 2, 1, 'h', '2026-08-06T14:57:37Z', 'active')`,
    ).run();
    db.close();
    const html = await renderHomePage();
    expect(html).toContain("Official forward experiment live since");
    expect(html).toContain("strategy v2");
    expect(html).not.toContain("No official experiment has launched yet");
  });

  it("links to every live page", async () => {
    const db = await migrateOnly();
    db.close();
    const html = await renderHomePage();
    for (const href of ["/performance", "/candidates", "/universe", "/health", "/changelog"]) {
      expect(html).toContain(`href="${href}"`);
    }
  });
});
