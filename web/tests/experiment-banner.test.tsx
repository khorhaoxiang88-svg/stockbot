/**
 * The experiment banner: "not yet launched" before pipeline/launch/
 * open_experiment.py has run, and the launch date + strategy version once
 * it has.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import Database from "better-sqlite3";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const REPO_ROOT = path.resolve(__dirname, "..", "..");

let tempDir: string;

async function renderBanner(dbPath: string): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { ExperimentBanner } = await import("@/components/experiment-banner");
  const element = await ExperimentBanner();
  return renderToStaticMarkup(element);
}

function migratedDb(dbPath: string) {
  const db = new Database(dbPath);
  for (const file of fs
    .readdirSync(path.join(REPO_ROOT, "migrations"))
    .filter((name: string) => name.endsWith(".up.sql"))
    .sort()) {
    db.exec(fs.readFileSync(path.join(REPO_ROOT, "migrations", file), "utf-8"));
  }
  return db;
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-banner-"));
});

afterEach(() => {
  vi.doUnmock("@/lib/paths");
  fs.rmSync(tempDir, { recursive: true, force: true });
});

describe("experiment banner", () => {
  it("shows not-yet-launched when the database does not exist", async () => {
    const html = await renderBanner(path.join(tempDir, "missing.db"));
    expect(html).toContain("No official experiment has launched yet");
  });

  it("shows not-yet-launched when no experiment row exists", async () => {
    const dbPath = path.join(tempDir, "empty.db");
    migratedDb(dbPath).close();
    const html = await renderBanner(dbPath);
    expect(html).toContain("No official experiment has launched yet");
  });

  it("shows the launch date and strategy version once an experiment is active", async () => {
    const dbPath = path.join(tempDir, "launched.db");
    const db = migratedDb(dbPath);
    db.prepare(
      "INSERT INTO experiments (experiment_id, strategy_version, "
      + "selection_rule_version, protocol_version, config_hash, started_at, status) "
      + "VALUES ('exp-test1', 2, 2, 1, 'somehash', '2026-08-10T14:00:00Z', 'active')",
    ).run();
    db.close();

    const html = await renderBanner(dbPath);
    expect(html).toContain("exp-test1");
    expect(html).toContain("strategy v2");
    expect(html).toContain("selection rule v2");
    expect(html).not.toContain("No official experiment has launched yet");
  });

  it("does not show the active banner once the experiment has ended", async () => {
    const dbPath = path.join(tempDir, "ended.db");
    const db = migratedDb(dbPath);
    db.prepare(
      "INSERT INTO experiments (experiment_id, strategy_version, "
      + "selection_rule_version, protocol_version, config_hash, started_at, ended_at, "
      + "status) VALUES ('exp-old', 2, 2, 1, 'somehash', '2026-08-10T14:00:00Z', "
      + "'2026-09-01T00:00:00Z', 'ended')",
    ).run();
    db.close();

    const html = await renderBanner(dbPath);
    // getActiveExperiment only ever selects status='active'.
    expect(html).toContain("No official experiment has launched yet");
  });
});
