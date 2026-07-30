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

export type XbrlFact = {
  fact_id: number;
  concept: string;
  taxonomy: string;
  unit: string | null;
  context_type: string | null;
  period_start: string | null;
  period_end: string | null;
  normalized_numeric_value: number | null;
  raw_value: string | null;
  fiscal_year: number | null;
  fiscal_period: string | null;
  form_type: string | null;
  accession_no: string | null;
  filed_date: string | null;
  accepted_at: string | null;
  semantic_hash: string;
  source_fact_key: string;
  source_endpoint: string;
  decimals: number | null;
  is_nil: number | null;
  dimensions_json: string | null;
};

export type FactsSummary = {
  total: number;
  usable: number;
  unusable: number;
  concepts: number;
  accessions: number;
  earliest: string | null;
  latest: string | null;
};

export function getFactsSummary(cik: string | null) {
  if (!cik) {
    return {
      status: { state: "ok", path: DB_PATH } as DbStatus,
      row: null as FactsSummary | null,
    };
  }
  const result = readAll<FactsSummary>(
    "xbrl_facts",
    `SELECT COUNT(*) AS total,
            SUM(CASE WHEN accepted_at IS NOT NULL THEN 1 ELSE 0 END) AS usable,
            SUM(CASE WHEN accepted_at IS NULL THEN 1 ELSE 0 END) AS unusable,
            COUNT(DISTINCT concept) AS concepts,
            COUNT(DISTINCT accession_no) AS accessions,
            MIN(period_end) AS earliest, MAX(period_end) AS latest
       FROM xbrl_facts WHERE cik = '${String(cik).replace(/'/g, "")}'`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export function getFacts(cik: string | null, concept?: string, limit = 60) {
  if (!cik) return { status: { state: "ok", path: DB_PATH } as DbStatus, rows: [] as XbrlFact[] };
  const safeCik = String(cik).replace(/'/g, "");
  const conceptClause = concept
    ? `AND concept = '${concept.replace(/'/g, "")}'`
    : "";
  return readAll<XbrlFact>(
    "xbrl_facts",
    `SELECT fact_id, concept, taxonomy, unit, context_type, period_start, period_end,
            normalized_numeric_value, raw_value, fiscal_year, fiscal_period, form_type,
            accession_no, filed_date, accepted_at, semantic_hash, source_fact_key,
            source_endpoint, decimals, is_nil, dimensions_json
       FROM xbrl_facts
      WHERE cik = '${safeCik}' ${conceptClause}
      ORDER BY period_end DESC, concept, filed_date DESC
      LIMIT ${Number.isInteger(limit) && limit > 0 ? limit : 60}`,
  );
}

/** Facts whose meaning matches but which came from different filings. */
export function getRestatements(cik: string | null, limit = 8) {
  if (!cik) {
    return {
      status: { state: "ok", path: DB_PATH } as DbStatus,
      rows: [] as { semantic_hash: string; concept: string; unit: string | null;
        period_start: string | null; period_end: string | null;
        n_rows: number; n_accessions: number; n_values: number }[],
    };
  }
  const safeCik = String(cik).replace(/'/g, "");
  return readAll<{
    semantic_hash: string;
    concept: string;
    unit: string | null;
    period_start: string | null;
    period_end: string | null;
    n_rows: number;
    n_accessions: number;
    n_values: number;
  }>(
    "xbrl_facts",
    `SELECT semantic_hash, concept, unit, period_start, period_end,
            COUNT(*) AS n_rows,
            COUNT(DISTINCT accession_no) AS n_accessions,
            COUNT(DISTINCT normalized_numeric_value) AS n_values
       FROM xbrl_facts
      WHERE cik = '${safeCik}'
      GROUP BY semantic_hash
     HAVING n_accessions > 1 AND n_values > 1
      ORDER BY n_values DESC, period_end DESC
      LIMIT ${Number.isInteger(limit) && limit > 0 ? limit : 8}`,
  );
}

export function getFactsBySemanticHash(semanticHash: string) {
  return readAll<XbrlFact>(
    "xbrl_facts",
    `SELECT fact_id, concept, taxonomy, unit, context_type, period_start, period_end,
            normalized_numeric_value, raw_value, fiscal_year, fiscal_period, form_type,
            accession_no, filed_date, accepted_at, semantic_hash, source_fact_key,
            source_endpoint, decimals, is_nil, dimensions_json
       FROM xbrl_facts WHERE semantic_hash = '${semanticHash.replace(/'/g, "")}'
      ORDER BY filed_date`,
  );
}

export function getPayloadSummary() {
  const result = readAll<{
    n: number;
    bytes: number;
    sources: number;
    latest: string | null;
  }>(
    "raw_payloads",
    `SELECT COUNT(*) AS n, COALESCE(SUM(byte_size), 0) AS bytes,
            COUNT(DISTINCT identifier) AS sources, MAX(fetched_at) AS latest
       FROM raw_payloads`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export const SCALAR_METRICS = [
  "pe", "pb", "ev_ebitda", "fcf_yield", "roic", "interest_coverage",
  "debt_ebitda", "current_ratio", "gross_margin", "revenue_growth_yoy",
  "shares_outstanding",
] as const;

export const PIOTROSKI_METRICS = [
  "piotroski_roa_positive", "piotroski_cfo_positive", "piotroski_roa_improved",
  "piotroski_accruals", "piotroski_leverage_decreased",
  "piotroski_current_ratio_improved", "piotroski_no_new_shares",
  "piotroski_gross_margin_improved", "piotroski_asset_turnover_improved",
] as const;

export type MetricName =
  | (typeof SCALAR_METRICS)[number]
  | (typeof PIOTROSKI_METRICS)[number];

export type DerivedFundamentals = Record<string, string | number | null>;

export function getFundamentalPeriods(securityId: number) {
  return readAll<{ period_end: string; knowledge_date: string; states: number }>(
    "derived_fundamentals",
    `SELECT period_end, MAX(knowledge_date) AS knowledge_date, COUNT(*) AS states
       FROM derived_fundamentals WHERE security_id = ${Number(securityId) || 0}
      GROUP BY period_end ORDER BY period_end DESC`,
  );
}

export function getLatestFundamentals(securityId: number) {
  const result = readAll<DerivedFundamentals>(
    "derived_fundamentals",
    `SELECT * FROM derived_fundamentals WHERE security_id = ${Number(securityId) || 0}
      ORDER BY period_end DESC, knowledge_date DESC LIMIT 1`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

/** Every knowledge state for one period, oldest first. An amendment adds a row. */
export function getKnowledgeStates(securityId: number, periodEnd: string) {
  return readAll<DerivedFundamentals>(
    "derived_fundamentals",
    `SELECT * FROM derived_fundamentals
      WHERE security_id = ${Number(securityId) || 0}
        AND period_end = '${periodEnd.replace(/'/g, "")}'
      ORDER BY knowledge_date`,
  );
}

export type InsiderTransaction = {
  accession_no: string;
  line_no: number;
  insider_name: string | null;
  insider_cik: string | null;
  role_officer: number;
  role_director: number;
  role_ten_percent: number;
  officer_title: string | null;
  transaction_date: string | null;
  filed_date: string | null;
  accepted_at: string | null;
  table_type: string;
  transaction_code: string | null;
  plan_status: string;
  plan_status_source: string;
  shares: number | null;
  price_per_share: number | null;
  total_value: number | null;
  shares_owned_after: number | null;
  is_amendment: number;
  amends_accession: string | null;
  superseded_by_accession: string | null;
};

const INSIDER_COLUMNS = `accession_no, line_no, insider_name, insider_cik, role_officer,
  role_director, role_ten_percent, officer_title, transaction_date, filed_date, accepted_at,
  table_type, transaction_code, plan_status, plan_status_source, shares, price_per_share,
  total_value, shares_owned_after, is_amendment, amends_accession, superseded_by_accession`;

/** Table I. Superseded rows are INCLUDED so the panel can grey them out. */
export function getInsiderTableOne(securityId: number, limit = 80) {
  return readAll<InsiderTransaction>(
    "insider_transactions",
    `SELECT ${INSIDER_COLUMNS} FROM insider_transactions
      WHERE security_id = ${Number(securityId) || 0} AND table_type = 'I'
      ORDER BY transaction_date DESC, accession_no, line_no
      LIMIT ${Number.isInteger(limit) && limit > 0 ? limit : 80}`,
  );
}

/** Table II, shown separately and never scored. */
export function getInsiderTableTwo(securityId: number, limit = 40) {
  return readAll<InsiderTransaction>(
    "insider_transactions",
    `SELECT ${INSIDER_COLUMNS} FROM insider_transactions
      WHERE security_id = ${Number(securityId) || 0} AND table_type = 'II'
      ORDER BY transaction_date DESC, accession_no, line_no
      LIMIT ${Number.isInteger(limit) && limit > 0 ? limit : 40}`,
  );
}

/** Distinct insiders with a scored open-market purchase, for cluster detection. */
export function getInsiderClusterSummary(securityId: number) {
  const result = readAll<{
    purchasers: number;
    purchases: number;
    first_date: string | null;
    last_date: string | null;
    total_value: number | null;
  }>(
    "insider_transactions",
    `SELECT COUNT(DISTINCT insider_cik) AS purchasers, COUNT(*) AS purchases,
            MIN(transaction_date) AS first_date, MAX(transaction_date) AS last_date,
            SUM(total_value) AS total_value
       FROM scored_insider_purchases
      WHERE security_id = ${Number(securityId) || 0}`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
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
