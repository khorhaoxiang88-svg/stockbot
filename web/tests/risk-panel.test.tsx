/**
 * The risk panel must be titled exactly "Measured risks and missing evidence",
 * must keep detected risks and unknowns in separate labelled sections, must link
 * every entry to its source, and must never present insider selling as bearish.
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

const FLAGS: [string, string, string, string | null, number][] = [
  [
    "going_concern",
    "high",
    "Substantial-doubt language located in 10-K 0000000001-26-000001 filed 2026-03-02, at character offset 412,900 of the extracted text.",
    "0000000001-26-000001",
    0,
  ],
  [
    "negative_operating_cash_flow",
    "high",
    "Operating cash flow for 2025-12-31 was -412.60M. The business consumed cash from operations over the full fiscal year.",
    "0000000001-26-000001",
    0,
  ],
  [
    "recent_reverse_split",
    "medium",
    "Reverse split with ex-date 2024-08-16, ratio 0.1 (1-for-10), recorded by yfinance.",
    "ledger:corporate_actions:1:2024-08-16",
    0,
  ],
  [
    "altman_distress",
    "unknown",
    "Could not determine: Z'' inputs missing for 2025-12-31: retained earnings",
    null,
    1,
  ],
  [
    "low_interest_coverage",
    "unknown",
    "Could not determine: interest coverage is not available.",
    null,
    1,
  ],
  [
    "recent_insider_selling",
    "context",
    "3 Table I sale(s) by 2 insider(s) in the last 90 days. CONTEXT ONLY: insiders sell for taxes, diversification and personal reasons.",
    "0000000001-26-000009",
    0,
  ],
  [
    "high_leverage",
    "none",
    "Not detected. Debt/EBITDA for 2025-12-31 was 0.63x, at or below the configured threshold of 4.0x.",
    "0000000001-26-000001",
    0,
  ],
];

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
     VALUES (1, '0000000001', NULL, 'Distressed Operating Co.', 'common_stock',
             'high', 'test', '3571', '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (1, 'DIST', 'NYSE', '2026-07-30', NULL, 1)`,
  ).run();
  db.prepare(
    `INSERT INTO filings (accession_no, cik, form_type, filed_date, accepted_at,
                          period_of_report, primary_doc_url, payload_id)
     VALUES ('0000000001-26-000001', '0000000001', '10-K', '2026-03-02',
             '2026-03-02T21:00:00Z', '2025-12-31',
             'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/dist-10k.htm', NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO filings (accession_no, cik, form_type, filed_date, accepted_at,
                          period_of_report, primary_doc_url, payload_id)
     VALUES ('0000000001-26-000009', '0000000001', '4', '2026-07-01',
             '2026-07-01T21:00:00Z', '2026-06-30',
             'https://www.sec.gov/Archives/edgar/data/1/000000000126000009/form4.xml', NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO corporate_actions (security_id, ex_date, action_type, ratio, cash_amount,
                                    provider, requires_manual_review)
     VALUES (1, '2024-08-16', 'split', 0.1, NULL, 'yfinance', 0)`,
  ).run();

  const insert = db.prepare(
    `INSERT INTO risk_flags (security_id, as_of_date, flag_code, severity, evidence_text,
                             source_accession, is_unknown)
     VALUES (1, '2026-07-30', ?, ?, ?, ?, ?)`,
  );
  for (const flag of FLAGS) insert.run(...flag);

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
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-risk-"));
  dbPath = path.join(tempDir, "test.db");
  await buildDatabase();
});

afterEach(() => {
  vi.doUnmock("@/lib/paths");
  try {
    fs.rmSync(tempDir, { recursive: true, force: true });
  } catch {
    // Windows holds a short lock on the SQLite file after close.
  }
});

describe("risk panel", () => {
  it("uses the exact required title", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Measured risks and missing evidence");
  });

  it("separates detected risks from unknowns with labelled sections", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Detected risks");
    expect(html).toContain("Could not determine");
    const detectedAt = html.indexOf("Detected risks");
    const unknownAt = html.indexOf(">Could not determine<");
    expect(detectedAt).toBeGreaterThan(-1);
    expect(unknownAt).toBeGreaterThan(detectedAt);
    // Counts are stated up front, so unknowns are visible without scrolling.
    expect(html).toContain("3 detected");
    expect(html).toContain("2 could not determine");
  });

  it("links every SEC-sourced entry to its filing", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain(
      "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/dist-10k.htm",
    );
    expect(html).toContain("10-K 0000000001-26-000001");
    expect(html).toContain("filed 2026-03-02");
  });

  it("says plainly when the source is the ledger rather than a filing", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("corporate-actions ledger");
    expect(html).toContain("not an SEC filing");
  });

  it("shows unknowns with their reason and never as a clean result", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Z&#x27;&#x27; inputs missing for 2025-12-31: retained earnings");
    expect(html).toContain("interest coverage is not available");
    expect(html).toContain("An empty section means the");
  });

  it("presents insider selling as context and never as bearish", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Context, not a risk signal");
    expect(html).toContain("CONTEXT ONLY");
    expect(html).toContain("taxes, diversification");
    // The insider entry must not be inside the detected-risk list.
    const detected = html.slice(
      html.indexOf("Detected risks"),
      html.indexOf("Could not determine"),
    );
    expect(detected).not.toContain("Recent insider selling");
  });

  it("keeps clean checks visible but out of the detected list", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("ran and detected");
    expect(html).toContain("at or below the configured threshold of 4.0x");
  });

  it("carries the same heading level as the composite score", async () => {
    const html = await renderSecurityPage("1");
    // Equal visual weight: both are h2 sections with lg badges.
    expect(html).toContain("<h2>Measured risks and missing evidence</h2>");
    expect(html).toContain("<h2>Composite score</h2>");
    const riskBadge = html.indexOf("3 detected");
    expect(html.slice(riskBadge - 400, riskBadge)).toContain("text-lg");
  });

  it("shows an empty state when nothing has been computed", async () => {
    const { default: Database } = await import("better-sqlite3");
    const db = new Database(dbPath);
    db.prepare("DELETE FROM risk_flags").run();
    db.close();
    const html = await renderSecurityPage("1");
    expect(html).toContain("Measured risks and missing evidence");
    expect(html).toContain("Run pipeline/riskflags/compute.py");
  });
});
