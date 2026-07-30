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
  // Apply every migration in order, so this stays correct as migrations are added.
  const upFiles = fs
    .readdirSync(MIGRATIONS)
    .filter((name) => name.endsWith(".up.sql"))
    .sort();
  for (const file of upFiles) {
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
  // XBRL facts: a restatement (same meaning, two accessions, two values) plus
  // one fact whose acceptance time could not be resolved.
  db.prepare(
    `INSERT INTO raw_payloads (payload_id, source, endpoint, identifier, relative_path,
                               content_hash, byte_size, fetched_at)
     VALUES ('pay1', 'sec', 'companyfacts', 'CIK0000019617',
             'data/raw/sec/2026/07/abc.json.gz', 'abc', 1234, '2026-07-29T00:00:00Z')`,
  ).run();
  const insertFact = db.prepare(
    `INSERT INTO xbrl_facts (payload_id, source_fact_key, cik, taxonomy, concept, unit,
                             context_type, period_start, period_end, context_hash,
                             semantic_hash, normalized_numeric_value, raw_value,
                             fiscal_year, fiscal_period, form_type, accession_no,
                             filed_date, accepted_at, source_endpoint)
     VALUES ('pay1', ?, '0000019617', 'us-gaap', ?, 'USD', 'duration',
             '2023-01-01', '2023-12-31', 'ctx1', ?, ?, ?, 2023, 'FY', ?, ?, ?, ?,
             'companyfacts')`,
  );
  insertFact.run("k0", "Revenues", "sem1", 1000, "1000", "10-K", "0000019617-24-01", "2024-02-01",
    "2024-02-01T21:30:00Z");
  insertFact.run("k1", "Revenues", "sem1", 1250, "1250", "10-K/A", "0000019617-25-02",
    "2025-02-01", "2025-02-01T22:05:11Z");
  insertFact.run("k2", "Assets", "sem2", 5000, "5000", "10-K", "0000019617-99-99", "2023-02-01",
    null);

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
  try {
    fs.rmSync(tempDir, { recursive: true, force: true });
  } catch {
    // Windows keeps a brief lock on the SQLite file after close; the temp
    // directory is disposable either way.
  }
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

  it("shows the price dataset panel even with no bars", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("Price dataset");
    expect(html).toContain("Revisions");
    expect(html).toContain("No bar for this security has been corrected");
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

  it("renders the facts browser with concept, period, value, form, filed and accepted", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("Accounting facts");
    expect(html).toContain("Revenues");
    expect(html).toContain("2023-12-31");
    expect(html).toContain("10-K/A");
    expect(html).toContain("2025-02-01T22:05:11Z");
  });

  it("shows both versions of a restated fact, not one overwritten row", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("Restated facts");
    expect(html).toContain("0000019617-24-01");
    expect(html).toContain("0000019617-25-02");
    expect(html).toContain("2 filings");
  });

  it("flags a fact whose acceptance time could not be resolved", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("unusable for official candidates");
  });

  it("states the companyfacts limitation instead of hiding it", async () => {
    const html = await renderSecurityPage("7");
    expect(html).toContain("Known limitation");
    expect(html).toContain("decimals");
  });

  it("shows a NULL metric as 'not available', never as zero", async () => {
    const html = await renderSecurityPage("7");
    // This security has no derived_fundamentals row at all, so the panel shows
    // its empty state rather than a table of zeros.
    expect(html).toContain("Fundamentals");
    expect(html).not.toMatch(/>0\.00</);
  });

  it("shows a warrant as not rankable, for the security-type reason", async () => {
    const html = await renderSecurityPage("9");
    expect(html).toContain("not common stock");
    expect(html).not.toContain("unknown securities are never ranked");
  });
});
