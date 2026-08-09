/**
 * News Ledger, Stage A: the panel must carry the shadow-mode banner on every
 * render, must separate binding / non-binding-or-rumor / could-not-classify
 * into their own labelled sections, must never infer an amount the filing
 * did not explicitly state, and must never link to /candidates or any
 * scoring concept -- Stage B does not exist yet.
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
     VALUES (1, '0000000001', NULL, 'News Co.', 'common_stock',
             'high', 'test', '3571', '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z', 1, NULL)`,
  ).run();
  db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (1, 'NEWS', 'NYSE', '2026-07-30', NULL, 1)`,
  ).run();

  db.prepare(
    `INSERT INTO news_filings (accession_no, cik, security_id, form_type, filed_date,
                               accepted_at, period_of_report, primary_doc_url, payload_id,
                               ingested_at)
     VALUES ('0000000001-26-000101', '0000000001', 1, '8-K', '2026-06-01',
             '2026-06-01T21:00:00Z', NULL,
             'https://www.sec.gov/Archives/edgar/data/1/000000000126000101/8k.htm',
             NULL, '2026-06-02T00:00:00Z')`,
  ).run();

  const insertEvent = db.prepare(
    `INSERT INTO news_events
       (event_id, security_id, accession_no, accepted_at, source_document, extracted_at,
        is_abstain, abstain_reason, event_type_candidate, confirmation_tier, amount_explicit,
        amount_stated, amount_type, currency, contract_duration_months, annualization_method,
        includes_optional_extensions, supporting_passage, passage_source_offset,
        extraction_model_version, extraction_prompt_version, supersedes_event_id)
     VALUES (?, 1, '0000000001-26-000101', '2026-06-01T21:00:00Z', '8k.htm', '2026-06-02T00:00:00Z',
             ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, 'claude-sonnet-5',
             'news-extract-v1', NULL)`,
  );

  insertEvent.run(
    "evt-binding", 0, null, "binding_commercial_contract", "binding", 1, 5_000_000, "total",
    "USD", "The Company entered into a five-year, $5,000,000 supply agreement.",
  );
  insertEvent.run(
    "evt-loi", 0, null, "non_binding_loi_or_mou", "non_binding_loi", 0, null, null, null,
    "The parties signed a non-binding letter of intent.",
  );
  insertEvent.run(
    "evt-abstain", 1, "board resignation, no economic content to classify", null, null, 0,
    null, null, null, "Item 5.02 Departure of Directors.",
  );

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
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-news-"));
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

describe("news ledger panel", () => {
  it("always carries the Stage A shadow-mode banner", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Stage A -- shadow mode");
    expect(html).toContain("Zero influence on any");
  });

  it("separates binding from non-binding/rumor into labelled sections", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Binding agreements");
    expect(html).toContain("Non-binding / rumor -- display only, never scored");
    const bindingAt = html.indexOf("Binding agreements");
    const nonBindingAt = html.indexOf("Non-binding / rumor");
    expect(bindingAt).toBeGreaterThan(-1);
    expect(nonBindingAt).toBeGreaterThan(bindingAt);
    expect(html).toContain("1 binding");
    expect(html).toContain("1 non-binding / rumor");
  });

  it("shows the could-not-classify event with its reason, never a guessed tier", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Could not classify");
    expect(html).toContain("board resignation, no economic content to classify");
    expect(html).toContain("1 could not classify");
  });

  it("never infers an amount the filing did not state", async () => {
    const html = await renderSecurityPage("1");
    // The binding event's explicit amount is shown.
    expect(html).toContain("5,000,000");
    // The LOI event carried no amount and must render as "not stated", not 0 or blank.
    const loiCard = html.slice(
      html.indexOf("non-binding letter of intent"),
      html.indexOf("non-binding letter of intent") + 1200,
    );
    expect(loiCard).toContain("not stated");
  });

  it("links the source accession and never links to /candidates or a score", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("https://www.sec.gov/Archives/edgar/data/1/000000000126000101/8k.htm");
    expect(html).toContain("0000000001-26-000101");

    const panel = html.slice(html.indexOf("<h2>News ledger</h2>"), html.indexOf("<h2>Universe membership</h2>"));
    expect(panel).not.toContain("/candidates");
    expect(panel).not.toContain("composite");
  });

  it("shows an empty state when nothing has been ingested", async () => {
    // news_events is append-only (migration 024) -- a fresh security with no
    // filings/events at all is the honest way to exercise this, not a
    // DELETE against security 1's already-committed rows.
    const { default: Database } = await import("better-sqlite3");
    const db = new Database(dbPath);
    db.prepare(
      `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                               classification_confidence, classification_source, sic_code,
                               first_seen, last_seen, is_active, delisted_date)
       VALUES (2, '0000000002', NULL, 'No News Co.', 'common_stock',
               'high', 'test', '3571', '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z', 1, NULL)`,
    ).run();
    db.prepare(
      `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
       VALUES (2, 'QUIET', 'NYSE', '2026-07-30', NULL, 1)`,
    ).run();
    db.close();
    const html = await renderSecurityPage("2");
    expect(html).toContain("Stage A -- shadow mode");
    expect(html).toContain("Run pipeline/news/ingest.py");
  });
});
