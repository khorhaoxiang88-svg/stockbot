/**
 * /changelog must render defect_log (migration 023), publish only rows with
 * published_at set, and show enough to distinguish cosmetic / data_correction
 * / material severity -- material being the one that compromises a strategy
 * version and starts a new, separately-reported one.
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

async function insertFrozenConfigLock(db: InstanceType<typeof import("better-sqlite3")>) {
  db.prepare(
    `INSERT INTO calibration_reports (report_id, computed_at, score_date, config_hash, report_json)
     VALUES ('calib-test', '2026-08-01T00:00:00Z', '2026-07-31', 'h', '{}')`,
  ).run();
  db.prepare(
    `INSERT INTO frozen_config_lock (strategy_version, selection_rule_version, config_hash,
                                     calibration_report_id, locked_at)
     VALUES (2, 2, 'h', 'calib-test', '2026-08-01T00:00:00Z'),
            (3, 2, 'h2', 'calib-test', '2026-08-10T00:00:00Z')`,
  ).run();
}

async function renderChangelogPage(): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { default: ChangelogPage } = await import("@/app/changelog/page");
  return renderToStaticMarkup(await ChangelogPage());
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-changelog-"));
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

describe("changelog page", () => {
  it("shows the correct empty state when no defect has ever been published", async () => {
    const db = await migrateOnly();
    db.close();
    const html = await renderChangelogPage();
    expect(html).toContain("No defect has been published yet");
    expect(html).toContain("not a missing feature");
  });

  it("does not show an unpublished (draft) defect", async () => {
    const db = await migrateOnly();
    db.prepare(
      `INSERT INTO defect_log (defect_id, discovered_at, severity, description)
       VALUES ('defect-draft', '2026-08-01T00:00:00Z', 'cosmetic', 'Chart axis label typo')`,
    ).run();
    db.close();
    const html = await renderChangelogPage();
    expect(html).not.toContain("defect-draft");
    expect(html).not.toContain("Chart axis label typo");
    expect(html).toContain("No defect has been published yet");
  });

  it("renders a published cosmetic defect", async () => {
    const db = await migrateOnly();
    db.prepare(
      `INSERT INTO defect_log (defect_id, discovered_at, severity, description, resolution, published_at)
       VALUES ('defect-cosmetic-1', '2026-08-01T00:00:00Z', 'cosmetic',
               'Performance page currency symbol misaligned on narrow screens.',
               'Fixed the flex layout; no experiment restart needed.', '2026-08-02T00:00:00Z')`,
    ).run();
    db.close();
    const html = await renderChangelogPage();
    expect(html).toContain("defect-cosmetic-1");
    expect(html).toContain("Cosmetic");
    expect(html).toContain("currency symbol misaligned");
    expect(html).toContain("Fixed the flex layout; no experiment restart needed.");
  });

  it("renders a published material defect naming the compromised and new strategy versions", async () => {
    const db = await migrateOnly();
    await insertFrozenConfigLock(db);
    db.prepare(
      `INSERT INTO defect_log (defect_id, discovered_at, severity, description,
                               affected_strategy_version, resolution, new_strategy_version, published_at)
       VALUES ('defect-material-1', '2026-08-05T00:00:00Z', 'material',
               'ATR window off-by-one in the scoring window boundary affected 3 official candidates.',
               2, 'Strategy v2 marked compromised. Corrected logic re-released as v3.', 3,
               '2026-08-06T00:00:00Z')`,
    ).run();
    db.close();
    const html = await renderChangelogPage();
    expect(html).toContain("defect-material-1");
    expect(html).toContain("Material");
    expect(html).toContain("official candidate affected");
    expect(html).toContain("affected strategy v2");
    expect(html).toContain("compromised");
    expect(html).toContain("new strategy v3");
    expect(html).toContain("separately reported");
  });

  it("marks an unresolved published defect as such rather than hiding the gap", async () => {
    const db = await migrateOnly();
    db.prepare(
      `INSERT INTO defect_log (defect_id, discovered_at, severity, description, published_at)
       VALUES ('defect-open-1', '2026-08-07T00:00:00Z', 'data_correction',
               'Duplicate source_health row for the same source_name found in an audit.',
               '2026-08-07T01:00:00Z')`,
    ).run();
    db.close();
    const html = await renderChangelogPage();
    expect(html).toContain("defect-open-1");
    expect(html).toContain("Resolution not yet recorded.");
  });
});
