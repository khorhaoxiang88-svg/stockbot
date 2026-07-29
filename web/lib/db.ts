import fs from "node:fs";
import Database from "better-sqlite3";

import { DB_PATH } from "./paths";

/**
 * Read-only access to the pipeline database.
 *
 * Phase 1 has no market data, and the database may not exist at all yet, so
 * every helper here degrades to "nothing to show" instead of throwing. The
 * health page is the one screen that must render even when everything else
 * is missing.
 */

export type DbStatus =
  | { state: "ok"; path: string }
  | { state: "missing"; path: string }
  | { state: "error"; path: string; message: string };

export type PipelineRun = {
  run_id: string;
  stage: string;
  started_at: string; // UTC ISO-8601
  finished_at: string | null; // UTC ISO-8601
  status: string;
  records_written: number;
  code_version: string | null;
  errors_json: string | null;
};

export type SourceHealth = {
  source_name: string;
  last_success: string | null; // UTC ISO-8601
  last_error: string | null; // UTC ISO-8601
  consecutive_failures: number;
  staleness_hours: number | null;
  coverage_pct: number | null;
};

export type AppliedMigration = {
  version: string;
  applied_at: string; // UTC ISO-8601
};

function openReadOnly(): Database.Database | null {
  if (!fs.existsSync(DB_PATH)) return null;
  return new Database(DB_PATH, { readonly: true, fileMustExist: true });
}

function tableExists(db: Database.Database, name: string): boolean {
  const row = db
    .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?")
    .get(name);
  return row !== undefined;
}

function readAll<T>(table: string, sql: string): { status: DbStatus; rows: T[] } {
  let db: Database.Database | null = null;
  try {
    db = openReadOnly();
    if (!db) return { status: { state: "missing", path: DB_PATH }, rows: [] };
    if (!tableExists(db, table)) {
      return { status: { state: "ok", path: DB_PATH }, rows: [] };
    }
    const rows = db.prepare(sql).all() as T[];
    return { status: { state: "ok", path: DB_PATH }, rows };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { status: { state: "error", path: DB_PATH, message }, rows: [] };
  } finally {
    db?.close();
  }
}

export function getSourceHealth() {
  return readAll<SourceHealth>(
    "source_health",
    `SELECT source_name, last_success, last_error, consecutive_failures,
            staleness_hours, coverage_pct
       FROM source_health
      ORDER BY source_name`,
  );
}

export function getRecentRuns(limit = 20) {
  return readAll<PipelineRun>(
    "pipeline_runs",
    `SELECT run_id, stage, started_at, finished_at, status, records_written,
            code_version, errors_json
       FROM pipeline_runs
      ORDER BY started_at DESC
      LIMIT ${Number.isInteger(limit) && limit > 0 ? limit : 20}`,
  );
}

export function getAppliedMigrations() {
  return readAll<AppliedMigration>(
    "schema_migrations",
    "SELECT version, applied_at FROM schema_migrations ORDER BY version",
  );
}

export type Security = {
  security_id: number;
  cik: string | null;
  share_class: string | null;
  name: string;
  security_type: string;
  classification_confidence: string;
  classification_source: string;
  sic_code: string | null;
  first_seen: string;
  last_seen: string;
  is_active: number;
  delisted_date: string | null;
};

export type Listing = {
  security_id: number;
  symbol: string;
  exchange: string;
  valid_from: string;
  valid_to: string | null;
  is_primary: number;
};

export type FixtureRow = {
  security_id: number;
  symbol_at_selection: string;
  inclusion_reason: string;
  category: string;
  manifest_version: string;
  name: string;
  security_type: string;
  classification_confidence: string;
  classification_source: string;
  cik: string | null;
  sic_code: string | null;
};

export type TypeCount = { security_type: string; n: number };
export type ConfidenceCount = { classification_confidence: string; n: number };

export function getFixtureRows() {
  return readAll<FixtureRow>(
    "fixture_manifest",
    `SELECT f.security_id, f.symbol_at_selection, f.inclusion_reason, f.category,
            f.manifest_version, s.name, s.security_type, s.classification_confidence,
            s.classification_source, s.cik, s.sic_code
       FROM fixture_manifest f
       JOIN securities s ON s.security_id = f.security_id
      ORDER BY f.category, f.symbol_at_selection`,
  );
}

/** Fixture counts by instrument type, for the health page. */
export function getFixtureTypeCounts() {
  return readAll<TypeCount>(
    "fixture_manifest",
    `SELECT s.security_type, COUNT(*) AS n
       FROM fixture_manifest f
       JOIN securities s ON s.security_id = f.security_id
      GROUP BY s.security_type
      ORDER BY n DESC, s.security_type`,
  );
}

/** Fixture counts by classification confidence, for the health page. */
export function getFixtureConfidenceCounts() {
  return readAll<ConfidenceCount>(
    "fixture_manifest",
    `SELECT s.classification_confidence, COUNT(*) AS n
       FROM fixture_manifest f
       JOIN securities s ON s.security_id = f.security_id
      GROUP BY s.classification_confidence
      ORDER BY CASE s.classification_confidence
                 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END`,
  );
}

export function getSecurityById(securityId: number) {
  const result = readAll<Security>(
    "securities",
    `SELECT * FROM securities WHERE security_id = ${Number(securityId) || 0}`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export function getListingsFor(securityId: number) {
  return readAll<Listing>(
    "listings",
    `SELECT * FROM listings WHERE security_id = ${Number(securityId) || 0}
      ORDER BY valid_from DESC`,
  );
}

export function getFixtureEntryFor(securityId: number) {
  const result = readAll<FixtureRow>(
    "fixture_manifest",
    `SELECT f.security_id, f.symbol_at_selection, f.inclusion_reason, f.category,
            f.manifest_version, s.name, s.security_type, s.classification_confidence,
            s.classification_source, s.cik, s.sic_code
       FROM fixture_manifest f
       JOIN securities s ON s.security_id = f.security_id
      WHERE f.security_id = ${Number(securityId) || 0}`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export type PriceBar = {
  date: string; // ET trading date
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  revision: number;
  price_data_version: number;
};

export type CorporateAction = {
  ex_date: string;
  action_type: string;
  ratio: number | null;
  cash_amount: number | null;
  provider: string;
  requires_manual_review: number;
};

export type PriceRevision = {
  date: string;
  revision: number;
  old_open: number | null;
  old_high: number | null;
  old_low: number | null;
  old_close: number | null;
  old_volume: number | null;
  new_open: number | null;
  new_high: number | null;
  new_low: number | null;
  new_close: number | null;
  new_volume: number | null;
  detected_at: string;
  accepted_at: string | null;
  provider: string;
  price_data_version_before: number | null;
  price_data_version_after: number | null;
};

export type DatasetVersion = {
  dataset_version: number;
  created_at: string;
  provider: string;
  reason: string;
  changed_row_count: number;
};

export function getPrices(securityId: number) {
  return readAll<PriceBar>(
    "prices",
    `SELECT date, open, high, low, close, volume, revision, price_data_version
       FROM prices WHERE security_id = ${Number(securityId) || 0}
      ORDER BY date`,
  );
}

export function getCorporateActions(securityId: number) {
  return readAll<CorporateAction>(
    "corporate_actions",
    `SELECT ex_date, action_type, ratio, cash_amount, provider, requires_manual_review
       FROM corporate_actions WHERE security_id = ${Number(securityId) || 0}
      ORDER BY ex_date`,
  );
}

export function getPriceRevisions(securityId: number) {
  return readAll<PriceRevision>(
    "price_revisions",
    `SELECT * FROM price_revisions WHERE security_id = ${Number(securityId) || 0}
      ORDER BY date DESC, revision DESC`,
  );
}

export function getCurrentDatasetVersion() {
  const result = readAll<DatasetVersion>(
    "price_dataset_versions",
    `SELECT dataset_version, created_at, provider, reason, changed_row_count
       FROM price_dataset_versions ORDER BY dataset_version DESC LIMIT 1`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export function getProvenance(securityId: number) {
  return readAll<{
    provider: string;
    valid_from: string;
    valid_to: string | null;
    switch_reason: string | null;
  }>(
    "price_series_provenance",
    `SELECT provider, valid_from, valid_to, switch_reason
       FROM price_series_provenance WHERE security_id = ${Number(securityId) || 0}
      ORDER BY valid_from`,
  );
}

/** SIC codes the fixture cares about naming. Mirrors classify.industry_label. */
export function industryLabel(sicCode: string | null): string | null {
  if (!sicCode) return null;
  const code = String(sicCode).trim();
  if (code === "6798") return "REIT";
  if (code.startsWith("602") || ["6020", "6021", "6022", "6035", "6036"].includes(code)) {
    return "Bank";
  }
  return null;
}
