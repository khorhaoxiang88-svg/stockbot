/**
 * /security/[id] must render identity, classification and confidence, and must
 * show empty sections for everything that has no data yet.
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
  for (const file of ["001_operations_tables.up.sql", "002_securities_identity.up.sql"]) {
    db.exec(fs.readFileSync(path.join(MIGRATIONS, file), "utf-8"));
  }

  db.prepare(
    `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                             classification_confidence, classification_source, sic_code,
                             first_seen, last_seen, is_active, delisted_date)
     VALUES (7, '0000019617', NULL, 'JP Morgan Chase & Co. Common Stock', 'common_stock',
             'high', 'nasdaq:name:common', '6021', '2026-07-29T00:00:00Z',
             '2026-07-29T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (7, 'JPM', 'NYSE', '2026-07-29', NULL, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO fixture_manifest (security_id, symbol_at_selection, inclusion_reason,
                                   category, added_at, manifest_version)
     VALUES (7, 'JPM', 'Money-centre bank, SIC 6021 confirmed.', 'bank',
             '2026-07-29T00:00:00Z', '1')`,
  ).run();

  // A preferred share and a warrant, both classified cleanly at high
  // confidence, so the rankability tests cannot pass by accident.
  db.prepare(
    `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                             classification_confidence, classification_source, sic_code,
                             first_seen, last_seen, is_active, delisted_date)
     VALUES (8, '0001253986', NULL,
             'Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock',
             'preferred_share', 'high', 'nasdaq:name:preferred', '6798',
             '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (8, 'ABR$D', 'NYSE', '2026-07-29', NULL, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                             classification_confidence, classification_source, sic_code,
                             first_seen, last_seen, is_active, delisted_date)
     VALUES (9, NULL, NULL, 'Armada Acquisition Corp. III - Warrant', 'warrant',
             'high', 'nasdaq:name:warrant', NULL,
             '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (9, 'AACIW', 'Nasdaq', '2026-07-29', NULL, 1)`,
  ).run();
  db.close();
}

async function renderSecurityPage(id: string): Promise<string> {
  vi.resetModules();
  vi.doMock("@/lib/paths", () => ({
    REPO_ROOT,
    DB_PATH: dbPath,
    CONFIG_PATH: path.join(REPO_ROOT, "config.frozen.json"),
  }));
  const { default: SecurityPage } = await import("@/app/security/[id]/page");
  const element = await SecurityPage({ params: Promise.resolve({ id }) });
  return renderToStaticMarkup(element);
}

beforeEach(async () => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-security-"));
  dbPath = path.join(tempDir, "test.db");
  await buildDatabase();
});

afterEach(() => {
  vi.doUnmock("@/lib/paths");
  fs.rmSync(tempDir, { recursive: true, force: true });
});

describe("security page", () => {
  it("renders identity, classification and confidence", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("JP Morgan Chase &amp; Co.");
    expect(html).toContain("0000019617"); // CIK
    expect(html).toContain("JPM"); // current symbol
    expect(html).toContain("high confidence");
    expect(html).toContain("common stock");
    expect(html).toContain("nasdaq:name:common");
    expect(html).toContain("6021");
    expect(html).toContain("Bank"); // SIC-derived industry label
  });

  it("shows the internal id, not the symbol, as the identity", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("not its identity");
  });

  it("renders empty sections for data that does not exist yet", async () => {
    const html = await renderSecurityPage("7");
    for (const section of [
      "Prices",
      "Corporate actions",
      "Fundamentals",
      "Universe membership",
      "Signals",
    ]) {
      expect(html).toContain(section);
    }
    // This fixture security has no price rows, so Prices and Corporate actions
    // show their own empty states alongside the three not-yet-built sections.
    expect(html.match(/No data yet/g)?.length).toBe(5);
  });

  it("shows the listing window", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("Listing history");
    expect(html).toContain("current"); // open-ended valid_to
  });

  it("404s for an id that does not exist", async () => {
    await expect(renderSecurityPage("9999")).rejects.toThrow();
  });

  it("404s for a non-numeric id", async () => {
    await expect(renderSecurityPage("not-a-number")).rejects.toThrow();
  });

  it("shows common stock as rankable", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("Rankable");
    expect(html).not.toContain("not common stock");
  });

  it("shows a preferred share as not rankable, for the security-type reason", async () => {
    const html = await renderSecurityPage("8");
    expect(html).toContain("high confidence"); // classified cleanly
    expect(html).toContain("not common stock"); // still excluded
    expect(html).not.toContain("unknown securities are never ranked");
  });

  it("shows the price dataset panel even with no bars", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("Price dataset");
    expect(html).toContain("Revisions");
    expect(html).toContain("No bar for this security has been corrected");
  });

  it("shows a warrant as not rankable, for the security-type reason", async () => {
    const html = await renderSecurityPage("9");
    expect(html).toContain("not common stock");
    expect(html).not.toContain("unknown securities are never ranked");
  });
});
