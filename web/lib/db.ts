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

export type DilutionSignal = {
  as_of_date: string;
  shares_yoy_growth: number | null;
  d1_capacity: number;
  d2_issuance: number;
  d3_structural: number;
  d4_realised: number;
  dilution_score: number;
  is_disqualified: number;
  evidence_json: string | null;
  classification_notes: string | null;
};

export type DilutionEvidence = {
  accession: string;
  form: string;
  filed_date: string;
  url: string | null;
  outcome: string;
  reason: string;
  scores: boolean;
  tier: string | null;
  unexpired?: boolean;
};

export function getDilutionSignal(securityId: number) {
  const result = readAll<DilutionSignal>(
    "dilution_signals",
    `SELECT as_of_date, shares_yoy_growth, d1_capacity, d2_issuance, d3_structural,
            d4_realised, dilution_score, is_disqualified, evidence_json, classification_notes
       FROM dilution_signals WHERE security_id = ${Number(securityId) || 0}
      ORDER BY as_of_date DESC LIMIT 1`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export type ScoreRow = {
  security_id: number;
  score_date: string;
  strategy_version: number;
  config_hash: string;
  mapping_version: string;
  price_dataset_version: number | null;
  price_snapshot_hash: string | null;
  value_score: number | null;
  quality_score: number | null;
  momentum_score: number | null;
  insider_bonus: number | null;
  dilution_penalty: number;
  composite_score: number | null;
  rank: number | null;
  cohort_id: string | null;
  rankable: number;
  withhold_reason: string | null;
  explanation_json: string;
};

/**
 * Submetric rows are what makes a score checkable by hand: nominal weight,
 * whether it was valid, the EFFECTIVE weight after renormalisation, which
 * population it was ranked against, and the resulting contribution.
 */
export type ScoreSubmetric = {
  metric: string;
  kind: "percentile" | "absolute";
  nominal_weight: number;
  effective_weight: number;
  valid: boolean;
  reason?: string | null;
  raw_value: number | null;
  percentile?: number | null;
  value_used: number | null;
  contribution: number | null;
  lower_is_better?: boolean;
  comparison?: {
    market_population: string;
    market_count: number;
    market_percentile: number | null;
    cohort_population: string | null;
    cohort_count: number;
    cohort_percentile: number | null;
    blend_weight_w: number;
    knowledge_cutoff: string;
    snapshot_id: string;
  };
  detail?: Record<string, unknown> | null;
};

export type ScoreComponent = {
  component: string;
  renormalised_share: number;
  effective_weight_sum: number;
  submetrics: ScoreSubmetric[];
};

export type PiotroskiSignal = {
  signal: string;
  test: string;
  passed: boolean | null;
  points: number | null;
  concept_used: string | null;
  accession: string | null;
};

export type ScoreExplanation = {
  security_id: number;
  symbol: string;
  name: string;
  score_date: string;
  knowledge_cutoff?: string;
  snapshot_id: string;
  cohort_id: string | null;
  cohort_label: string | null;
  cohort_basis?: string;
  universe_status?: string;
  provenance?: Record<string, unknown>;
  components?: Record<
    string,
    {
      weight: number;
      score: number | null;
      gate: string;
      population?: string;
      piotroski?: {
        complete: boolean;
        f_score: number | null;
        max_f_score: number;
        value_used: number | null;
        formula: string;
        period_end: string | null;
        prior_period_end: string | null;
        reason: string | null;
        signals: PiotroskiSignal[];
      };
      detail: ScoreComponent;
    }
  >;
  insider_bonus?: {
    value: number | null;
    formula: string;
    coverage: { complete: boolean; reason: string; note: string };
    qualifying_definition: string;
    qualifying_purchases: number;
    b1_cluster: Record<string, unknown>;
    b2_executive: Record<string, unknown>;
    b3_size: Record<string, unknown>;
    b4_conviction: Record<string, unknown>;
    sum_before_cap: number;
  };
  dilution_penalty?: Record<string, unknown>;
  composite?: {
    formula: string;
    terms?: { term: string; weight: number; component: number; contribution: number }[];
    unclamped?: number;
    clamped?: number;
  };
  rankable: boolean;
  withhold_reason: string | null;
  altman_z_note?: string;
  winsorisation_note?: string;
};

/** The most recent stored score for one security. */
export function getScore(securityId: number) {
  const result = readAll<ScoreRow>(
    "scores",
    `SELECT security_id, score_date, strategy_version, config_hash, mapping_version,
            price_dataset_version, price_snapshot_hash, value_score, quality_score,
            momentum_score, insider_bonus, dilution_penalty, composite_score,
            "rank", cohort_id, rankable, withhold_reason, explanation_json
       FROM scores WHERE security_id = ${Number(securityId) || 0}
      ORDER BY score_date DESC, strategy_version DESC LIMIT 1`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

/** How many securities were ranked on the same date, for "rank N of M". */
export function getRankedCount(scoreDate: string) {
  const safe = String(scoreDate).replace(/[^0-9-]/g, "");
  const result = readAll<{ n: number }>(
    "scores",
    `SELECT COUNT(*) AS n FROM scores WHERE rankable = 1 AND score_date = '${safe}'`,
  );
  return result.rows[0]?.n ?? 0;
}

export function parseExplanation(json: string | null): ScoreExplanation | null {
  if (!json) return null;
  try {
    return JSON.parse(json) as ScoreExplanation;
  } catch {
    return null;
  }
}

export type RiskSeverity = "high" | "medium" | "low" | "none" | "context" | "unknown";

export type RiskFlag = {
  flag_code: string;
  severity: RiskSeverity;
  evidence_text: string;
  source_accession: string | null;
  is_unknown: number;
  /** Joined from `filings` when the source is an SEC accession. */
  source_form: string | null;
  source_filed_date: string | null;
  source_url: string | null;
};

/**
 * Every risk flag for one security at its newest computed date.
 *
 * The join to `filings` is a LEFT join on purpose: recent_reverse_split cites
 * the corporate-action ledger rather than a filing, and the panel says so
 * instead of rendering a dead link.
 */
export function getRiskFlags(securityId: number) {
  const id = Number(securityId) || 0;
  const result = readAll<RiskFlag>(
    "risk_flags",
    `SELECT r.flag_code, r.severity, r.evidence_text, r.source_accession, r.is_unknown,
            f.form_type AS source_form, f.filed_date AS source_filed_date,
            f.primary_doc_url AS source_url
       FROM risk_flags r
       LEFT JOIN filings f ON f.accession_no = r.source_accession
      WHERE r.security_id = ${id}
        AND r.as_of_date = (SELECT MAX(as_of_date) FROM risk_flags WHERE security_id = ${id})
      ORDER BY CASE r.severity
                 WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2
                 WHEN 'context' THEN 3 WHEN 'unknown' THEN 4 ELSE 5 END,
               r.flag_code`,
  );
  return { status: result.status, rows: result.rows };
}

export function getRiskAsOf(securityId: number) {
  const result = readAll<{ as_of_date: string }>(
    "risk_flags",
    `SELECT MAX(as_of_date) AS as_of_date FROM risk_flags
      WHERE security_id = ${Number(securityId) || 0}`,
  );
  return result.rows[0]?.as_of_date ?? null;
}

/** Human labels for the flag codes. Kept beside the query so both stay in step. */
export const RISK_FLAG_LABELS: Record<string, string> = {
  negative_operating_cash_flow: "Negative operating cash flow",
  negative_free_cash_flow: "Negative free cash flow",
  high_leverage: "High leverage",
  low_interest_coverage: "Low interest coverage",
  rapid_share_growth: "Rapid share growth",
  shelf_capacity: "Shelf capacity",
  active_issuance: "Active issuance",
  atm_or_convertible: "ATM programme or convertible",
  recent_reverse_split: "Recent reverse split",
  altman_distress: "Altman Z'' distress",
  going_concern: "Going concern",
  stale_or_incomplete_data: "Stale or incomplete data",
  recent_insider_selling: "Recent insider selling",
};

export type SelectionRun = {
  run_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  records_written: number;
  code_version: string | null;
};

export type ResearchCandidate = {
  candidate_id: string;
  security_id: number;
  symbol: string | null;
  name: string | null;
  generated_at: string;
  data_cutoff_at: string;
  snapshot_id: string;
  pipeline_run_id: string;
  strategy_version: number;
  config_hash: string;
  code_version: string;
  selection_rule_version: number;
  mapping_version: string;
  price_dataset_version: number | null;
  price_snapshot_hash: string | null;
  source_health_snapshot_json: string;
  score_snapshot_json: string;
  accessions_used_json: string;
  composite_at_generation: number;
  rank_at_generation: number;
  signal_close: number;
  atr_value: number | null;
  atr_window: number;
  price_data_cutoff: string;
  entry_rule: string;
  gap_limit_atr: number;
  row_hash: string;
};

export type SuppressedSignal = {
  security_id: number;
  symbol: string | null;
  horizon_days: number;
  composite: number | null;
  rank: number | null;
  suppression_reason: string;
  detail: string | null;
};

export type Book = {
  book_id: string;
  horizon_days: number;
  starting_nav: number;
  current_nav: number;
  open_position_count: number;
  strategy_version: number;
};

/** The newest selection run, whether or not it produced any candidate. */
export function getLatestSelectionRun() {
  const result = readAll<SelectionRun>(
    "pipeline_runs",
    `SELECT run_id, started_at, finished_at, status, records_written, code_version
       FROM pipeline_runs WHERE stage = 'selection'
      ORDER BY started_at DESC LIMIT 1`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export function getCandidatesForRun(runId: string) {
  const safe = String(runId).replace(/[^A-Za-z0-9_-]/g, "");
  const result = readAll<ResearchCandidate>(
    "research_candidates",
    `SELECT c.*, l.symbol, s.name
       FROM research_candidates c
       JOIN securities s ON s.security_id = c.security_id
       LEFT JOIN listings l ON l.security_id = c.security_id AND l.valid_to IS NULL
      WHERE c.pipeline_run_id = '${safe}'
      ORDER BY c.rank_at_generation`,
  );
  return { status: result.status, rows: result.rows };
}

export function getSuppressionsForRun(runId: string) {
  const safe = String(runId).replace(/[^A-Za-z0-9_-]/g, "");
  const result = readAll<SuppressedSignal>(
    "suppressed_signals",
    `SELECT p.security_id, l.symbol, p.horizon_days, p.composite, p."rank",
            p.suppression_reason, p.detail
       FROM suppressed_signals p
       LEFT JOIN listings l ON l.security_id = p.security_id AND l.valid_to IS NULL
      WHERE p.run_id = '${safe}'
      ORDER BY p.suppression_reason, p.composite DESC, p.security_id, p.horizon_days`,
  );
  return { status: result.status, rows: result.rows };
}

export function getBooks() {
  const result = readAll<Book>(
    "books",
    `SELECT book_id, horizon_days, starting_nav, current_nav, open_position_count,
            strategy_version
       FROM books ORDER BY horizon_days`,
  );
  return { status: result.status, rows: result.rows };
}

/** Plain-language labels for the suppression reasons. */
export const SUPPRESSION_LABELS: Record<string, string> = {
  not_rankable: "No composite score at the cutoff",
  model_not_applicable: "Model not supported for this security type",
  dilution_disqualified: "Disqualified by the dilution score",
  risk_flag_going_concern: "Severity-high going-concern flag",
  risk_flag_dilution_disqualify: "Severity-high dilution flag",
  below_composite_threshold: "Composite below the configured threshold",
  composite_threshold_unset: "Composite threshold is still unset",
  stale_source: "A source failed its freshness check",
  cooldown_recent_exit: "Cooldown: a position exited recently",
  cooldown_gap_cancelled: "Cooldown: gap-cancelled recently",
  open_position: "A position is already open at this horizon",
  book_capacity: "The book has no remaining capacity",
  cohort_cap: "Cohort already at its maximum",
  selection_cap: "The weekly candidate maximum was already filled",
};

export type PaperPosition = {
  position_id: string;
  candidate_id: string;
  horizon_days: number;
  book_id: string;
  symbol: string | null;
  security_id: number;
  cohort_id: string | null;
  entry_date: string;
  entry_price: number;
  slippage_bps: number;
  shares: number;
  notional: number;
  stop_price: number;
  target_price: number;
  status: string;
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  dividends_received: number;
  splits_applied: number;
  gross_pnl: number | null;
  net_pnl: number | null;
  pnl_pct: number | null;
  requires_manual_review: number;
};

export type BenchmarkPosition = {
  position_id: string;
  candidate_id: string;
  horizon_days: number;
  entry_date: string;
  entry_price: number;
  status: string;
  exit_date: string | null;
  exit_price: number | null;
  net_pnl: number | null;
  pnl_pct: number | null;
};

export type CancelledEntry = {
  candidate_id: string;
  symbol: string | null;
  reason: string;
  signal_close: number | null;
  next_open: number | null;
  gap_atr: number | null;
  adjusted_basis: string;
  cancelled_at: string;
};

/** Every paper position for one horizon, joined to the candidate's cohort and symbol. */
export function getPaperPositions(horizonDays: number) {
  const h = Number(horizonDays) || 0;
  const result = readAll<PaperPosition>(
    "paper_positions",
    `SELECT p.position_id, p.candidate_id, p.horizon_days, p.book_id,
            l.symbol, c.security_id,
            json_extract(c.score_snapshot_json, '$.cohort_id') AS cohort_id,
            p.entry_date, p.entry_price, p.slippage_bps, p.shares, p.notional,
            p.stop_price, p.target_price, p.status, p.exit_date, p.exit_price,
            p.exit_reason, p.dividends_received, p.splits_applied, p.gross_pnl,
            p.net_pnl, p.pnl_pct, p.requires_manual_review
       FROM paper_positions p
       JOIN research_candidates c ON c.candidate_id = p.candidate_id
       LEFT JOIN listings l ON l.security_id = c.security_id AND l.valid_to IS NULL
      WHERE p.horizon_days = ${h}
      ORDER BY p.entry_date, p.position_id`,
  );
  return { status: result.status, rows: result.rows };
}

export function getBenchmarkPositions(horizonDays: number) {
  const h = Number(horizonDays) || 0;
  const result = readAll<BenchmarkPosition>(
    "benchmark_positions",
    `SELECT position_id, candidate_id, horizon_days, entry_date, entry_price,
            status, exit_date, exit_price, net_pnl, pnl_pct
       FROM benchmark_positions WHERE horizon_days = ${h}
      ORDER BY entry_date, position_id`,
  );
  return { status: result.status, rows: result.rows };
}

export function getCancelledEntries() {
  const result = readAll<CancelledEntry>(
    "cancelled_entries",
    `SELECT ce.candidate_id, l.symbol, ce.reason, ce.signal_close, ce.next_open,
            ce.gap_atr, ce.adjusted_basis, ce.cancelled_at
       FROM cancelled_entries ce
       JOIN research_candidates c ON c.candidate_id = ce.candidate_id
       LEFT JOIN listings l ON l.security_id = c.security_id AND l.valid_to IS NULL
      ORDER BY ce.cancelled_at DESC`,
  );
  return { status: result.status, rows: result.rows };
}

/** Every distinct research_candidate that entry has been attempted for at all
 * (filled into at least one book, or cancelled) — the "unique originating
 * candidates" count the two-books rule requires never be confused with a
 * position count. */
export function getUniqueOriginatingCandidateCount() {
  const result = readAll<{ n: number }>(
    "research_candidates",
    `SELECT COUNT(DISTINCT candidate_id) AS n FROM (
        SELECT candidate_id FROM paper_positions
        UNION
        SELECT candidate_id FROM cancelled_entries)`,
  );
  return result.rows[0]?.n ?? 0;
}

export const EXECUTION_PROTOCOL_VERSION = "R1-PROTOCOL-1.1";

// ------------------------------------------------------------- F12: verification

export type VerificationResult = {
  run_id: string;
  check_number: number;
  check_name: string;
  status: "pass" | "fail" | "pending";
  detail: string;
  evidence_json: string;
};

export type VerificationRun = {
  run_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
};

/** The most recent verification run's ten check rows, in check order. */
export function getLatestVerificationResults() {
  const result = readAll<VerificationResult>(
    "verification_results",
    `SELECT run_id, check_number, check_name, status, detail, evidence_json
       FROM latest_verification_results ORDER BY check_number`,
  );
  return { status: result.status, rows: result.rows };
}

export function getLatestVerificationRun() {
  const result = readAll<VerificationRun>(
    "pipeline_runs",
    `SELECT run_id, started_at, finished_at, status FROM pipeline_runs
      WHERE stage = 'verification' ORDER BY started_at DESC LIMIT 1`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export function getFilingVerificationCount() {
  const result = readAll<{
    total: number; matching: number; amendments_matching: number; mismatches: number;
  }>(
    "filing_verifications",
    `SELECT COUNT(*) AS total,
            SUM(CASE WHEN matches_source = 1 THEN 1 ELSE 0 END) AS matching,
            SUM(CASE WHEN matches_source = 1 AND is_amendment = 1 THEN 1 ELSE 0 END)
              AS amendments_matching,
            SUM(CASE WHEN matches_source = 0 THEN 1 ELSE 0 END) AS mismatches
       FROM filing_verifications`,
  );
  return result.rows[0] ?? { total: 0, matching: 0, amendments_matching: 0, mismatches: 0 };
}

// -------------------------------------------------------------------- F12: debug

export type DebugTable = {
  table: string;
  label: string;
  columns: string[];
  rows: Record<string, unknown>[];
  totalCount: number;
  truncated: boolean;
};

const DEBUG_ROW_CAP = 500;

/**
 * Every stored row for one security, across every table that references it --
 * directly by security_id, or indirectly through cik or candidate_id. This is
 * a raw dump, not a curated view: the point is inspecting actual database
 * state without reading the code that produced it, so nothing here reshapes
 * or interprets a value the way the rest of the app's pages do.
 *
 * Tables are capped at DEBUG_ROW_CAP rows each with the true total always
 * shown, never silently. Every fixture-scale table (fewer than 1,000 rows for
 * any one security) renders in full; only prices and xbrl_facts can plausibly
 * exceed the cap for a security with years of history.
 */
export function getDebugTablesForSecurity(securityId: number): {
  status: DbStatus;
  cik: string | null;
  tables: DebugTable[];
} {
  const id = Number(securityId) || 0;
  let db: Database.Database | null = null;
  try {
    db = openReadOnly();
    if (!db) return { status: { state: "missing", path: DB_PATH }, cik: null, tables: [] };

    const securityRow = tableExists(db, "securities")
      ? (db.prepare("SELECT cik FROM securities WHERE security_id = ?").get(id) as
          | { cik: string | null }
          | undefined)
      : undefined;
    const cik = securityRow?.cik ?? null;

    const specs: { table: string; label: string; sql: string; params: unknown[] }[] = [
      { table: "securities", label: "Identity", sql:
        "SELECT * FROM securities WHERE security_id = ?", params: [id] },
      { table: "listings", label: "Symbol history", sql:
        "SELECT * FROM listings WHERE security_id = ? ORDER BY valid_from DESC", params: [id] },
      { table: "universe_snapshots", label: "Universe snapshots", sql:
        "SELECT * FROM universe_snapshots WHERE security_id = ? ORDER BY snapshot_date DESC",
        params: [id] },
      { table: "fixture_manifest", label: "Fixture manifest", sql:
        "SELECT * FROM fixture_manifest WHERE security_id = ?", params: [id] },
      { table: "prices", label: "Raw prices", sql:
        "SELECT * FROM prices WHERE security_id = ? ORDER BY date DESC", params: [id] },
      { table: "corporate_actions", label: "Corporate actions", sql:
        "SELECT * FROM corporate_actions WHERE security_id = ? ORDER BY ex_date DESC",
        params: [id] },
      { table: "price_revisions", label: "Price revisions", sql:
        "SELECT * FROM price_revisions WHERE security_id = ? ORDER BY date DESC, revision DESC",
        params: [id] },
      { table: "price_series_provenance", label: "Price series provenance", sql:
        "SELECT * FROM price_series_provenance WHERE security_id = ? ORDER BY valid_from DESC",
        params: [id] },
      { table: "filings", label: "SEC filings (by CIK)", sql:
        "SELECT * FROM filings WHERE cik = ? ORDER BY filed_date DESC", params: [cik ?? ""] },
      { table: "xbrl_facts", label: "XBRL facts (by CIK)", sql:
        "SELECT * FROM xbrl_facts WHERE cik = ? ORDER BY period_end DESC", params: [cik ?? ""] },
      { table: "derived_fundamentals", label: "Derived fundamentals", sql:
        "SELECT * FROM derived_fundamentals WHERE security_id = ? "
        + "ORDER BY period_end DESC, knowledge_date DESC", params: [id] },
      { table: "insider_transactions", label: "Insider transactions", sql:
        "SELECT * FROM insider_transactions WHERE security_id = ? ORDER BY transaction_date DESC",
        params: [id] },
      { table: "dilution_signals", label: "Dilution signals", sql:
        "SELECT * FROM dilution_signals WHERE security_id = ? ORDER BY as_of_date DESC",
        params: [id] },
      { table: "scores", label: "Composite scores", sql:
        "SELECT * FROM scores WHERE security_id = ? ORDER BY score_date DESC", params: [id] },
      { table: "risk_flags", label: "Risk flags", sql:
        "SELECT * FROM risk_flags WHERE security_id = ? ORDER BY as_of_date DESC, flag_code",
        params: [id] },
      { table: "research_candidates", label: "Research candidates", sql:
        "SELECT * FROM research_candidates WHERE security_id = ? ORDER BY data_cutoff_at DESC",
        params: [id] },
      { table: "suppressed_signals", label: "Suppressed signals", sql:
        "SELECT * FROM suppressed_signals WHERE security_id = ? ORDER BY run_id DESC",
        params: [id] },
      { table: "paper_positions", label: "Paper positions", sql:
        "SELECT p.* FROM paper_positions p "
        + "JOIN research_candidates c ON c.candidate_id = p.candidate_id "
        + "WHERE c.security_id = ? ORDER BY p.entry_date DESC", params: [id] },
      { table: "benchmark_positions", label: "Benchmark (SPY) positions", sql:
        "SELECT * FROM benchmark_positions WHERE security_id = ? ORDER BY entry_date DESC",
        params: [id] },
      { table: "cancelled_entries", label: "Cancelled entries", sql:
        "SELECT ce.* FROM cancelled_entries ce "
        + "JOIN research_candidates c ON c.candidate_id = ce.candidate_id "
        + "WHERE c.security_id = ? ORDER BY ce.cancelled_at DESC", params: [id] },
      { table: "position_events", label: "Position events (splits/dividends/etc.)", sql:
        "SELECT pe.* FROM position_events pe "
        + "JOIN paper_positions p ON p.position_id = pe.position_id "
        + "JOIN research_candidates c ON c.candidate_id = p.candidate_id "
        + "WHERE c.security_id = ? ORDER BY pe.ex_date DESC", params: [id] },
      { table: "filing_verifications", label: "Form 4 hand verifications", sql:
        "SELECT * FROM filing_verifications WHERE security_id = ? ORDER BY verified_at DESC",
        params: [id] },
    ];

    const tables: DebugTable[] = [];
    for (const spec of specs) {
      if (!tableExists(db, spec.table)) continue;
      if (spec.table === "filings" || spec.table === "xbrl_facts") {
        if (!cik) {
          tables.push({ table: spec.table, label: spec.label, columns: [], rows: [],
            totalCount: 0, truncated: false });
          continue;
        }
      }
      const totalRow = db
        .prepare(spec.sql.replace(/^SELECT [\s\S]*? FROM/, "SELECT COUNT(*) AS n FROM"))
        .get(...spec.params) as { n: number };
      const rows = db.prepare(`${spec.sql} LIMIT ${DEBUG_ROW_CAP}`).all(...spec.params) as
        Record<string, unknown>[];
      tables.push({
        table: spec.table, label: spec.label,
        columns: rows.length ? Object.keys(rows[0]) : [],
        rows, totalCount: totalRow.n, truncated: totalRow.n > rows.length,
      });
    }

    return { status: { state: "ok", path: DB_PATH }, cik, tables };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { status: { state: "error", path: DB_PATH, message }, cik: null, tables: [] };
  } finally {
    db?.close();
  }
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

// ------------------------------------------------------- S1 rules-based universe

export type UniverseSnapshotRun = {
  snapshot_id: string;
  effective_at: string; // UTC ISO-8601
  rules_version: string;
  config_hash: string;
  security_count: number;
  is_official: number;
  run_type: "monthly_membership" | "daily_safety";
};

export type UniverseMember = {
  security_id: number;
  symbol: string | null;
  name: string;
  status: "included" | "excluded" | "watch";
  exclusion_reason: string | null;
  adv_dollar: number | null;
  market_cap: number | null;
  market_cap_confidence: "high" | "medium" | "low" | null;
  days_below_retention: number;
};

export type UniverseMembershipChange = {
  change_id: string;
  security_id: number;
  symbol: string | null;
  name: string;
  change_type: "entered" | "exited";
  effective_date: string;
  previous_status: string | null;
  new_status: string;
  reason: string;
  recorded_at: string;
};

/** The newest snapshot of the given run_type, whichever securities it covers. */
export function getLatestUniverseSnapshotRun(runType: "monthly_membership" | "daily_safety") {
  const safe = runType === "daily_safety" ? "daily_safety" : "monthly_membership";
  const result = readAll<UniverseSnapshotRun>(
    "universe_snapshot_runs",
    `SELECT snapshot_id, effective_at, rules_version, config_hash, security_count,
            is_official, run_type
       FROM universe_snapshot_runs WHERE run_type = '${safe}'
      ORDER BY effective_at DESC LIMIT 1`,
  );
  return { status: result.status, row: result.rows[0] ?? null };
}

export function getUniverseSnapshotRows(snapshotId: string) {
  const safe = String(snapshotId).replace(/[^A-Za-z0-9_-]/g, "");
  const result = readAll<UniverseMember>(
    "universe_snapshots",
    `SELECT sn.security_id, l.symbol, s.name, sn.status, sn.exclusion_reason,
            sn.adv_dollar, sn.market_cap, sn.market_cap_confidence, sn.days_below_retention
       FROM universe_snapshots sn
       JOIN securities s ON s.security_id = sn.security_id
       LEFT JOIN listings l ON l.security_id = sn.security_id AND l.valid_to IS NULL
      WHERE sn.snapshot_id = '${safe}'
      ORDER BY (sn.status = 'included') DESC, s.name`,
  );
  return { status: result.status, rows: result.rows };
}

export function getUniverseMembershipChanges(limit = 200) {
  const safeLimit = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : 200;
  const result = readAll<UniverseMembershipChange>(
    "universe_membership_changes",
    `SELECT c.change_id, c.security_id, l.symbol, s.name, c.change_type, c.effective_date,
            c.previous_status, c.new_status, c.reason, c.recorded_at
       FROM universe_membership_changes c
       JOIN securities s ON s.security_id = c.security_id
       LEFT JOIN listings l ON l.security_id = c.security_id AND l.valid_to IS NULL
      ORDER BY c.recorded_at DESC LIMIT ${safeLimit}`,
  );
  return { status: result.status, rows: result.rows };
}

/** How many of the 50 Phase F fixture securities appear, included or excluded,
 * in the given snapshot -- the manual verification checklist's item 3. */
export function getFixtureCoverageInSnapshot(snapshotId: string) {
  const safe = String(snapshotId).replace(/[^A-Za-z0-9_-]/g, "");
  const result = readAll<{ n: number }>(
    "universe_snapshots",
    `SELECT COUNT(DISTINCT f.security_id) AS n
       FROM fixture_manifest f
       JOIN universe_snapshots sn ON sn.security_id = f.security_id AND sn.snapshot_id = '${safe}'`,
  );
  const fixtureTotal = readAll<{ n: number }>(
    "fixture_manifest",
    `SELECT COUNT(DISTINCT security_id) AS n FROM fixture_manifest`,
  );
  return {
    status: result.status,
    covered: result.rows[0]?.n ?? 0,
    total: fixtureTotal.rows[0]?.n ?? 0,
  };
}

// ------------------------------------------------------- S2: coverage reporting

export type SourceCoverage = { source: string; covered: number; total: number; pct: number };
export type MetricCoverage = {
  metric: string;
  validCount: number;
  total: number;
  pct: number;
  nullReasons: { reason: string; count: number }[];
};
export type StalenessBucket = { bucket: string; count: number };
export type WorstCoverageRow = {
  security_id: number;
  symbol: string | null;
  sourcesPresent: number;
  sourcesTotal: number;
  missing: string[];
};

const COVERAGE_SOURCES: { key: string; table: string; joinOnCik: boolean }[] = [
  { key: "prices", table: "prices", joinOnCik: false },
  { key: "form4", table: "insider_transactions", joinOnCik: false },
  { key: "xbrl", table: "xbrl_facts", joinOnCik: true },
  { key: "fundamentals", table: "derived_fundamentals", joinOnCik: false },
];

/** Per source and per metric coverage across the given snapshot's population
 * (everyone evaluated, included or excluded), plus staleness and the
 * securities with the worst coverage. "Valid data" means at least one row in
 * the relevant table for prices/form4/xbrl/fundamentals-any; a metric's
 * validity is its own value being non-null in the latest derived_fundamentals
 * row for that security. */
export function getCoverageReport(snapshotId: string) {
  const safe = String(snapshotId).replace(/[^A-Za-z0-9_-]/g, "");
  const population = readAll<{ security_id: number; symbol: string | null; cik: string | null }>(
    "universe_snapshots",
    `SELECT sn.security_id, l.symbol, s.cik
       FROM universe_snapshots sn
       JOIN securities s ON s.security_id = sn.security_id
       LEFT JOIN listings l ON l.security_id = sn.security_id AND l.valid_to IS NULL
      WHERE sn.snapshot_id = '${safe}'`,
  );
  if (population.rows.length === 0) {
    return {
      status: population.status, total: 0, bySource: [] as SourceCoverage[],
      byMetric: [] as MetricCoverage[], staleness: [] as StalenessBucket[],
      worst: [] as WorstCoverageRow[],
    };
  }
  const total = population.rows.length;
  const ids = population.rows.map((r) => r.security_id);
  const idList = ids.join(",");
  const bySecurity = new Map(population.rows.map((r) => [r.security_id, r]));

  const bySource: SourceCoverage[] = COVERAGE_SOURCES.map(({ key, table, joinOnCik }) => {
    const set: Set<string | number> = joinOnCik
      ? new Set(
          readAll<{ cik: string }>(
            table, `SELECT DISTINCT cik FROM ${table} WHERE cik IS NOT NULL`,
          ).rows.map((r) => r.cik),
        )
      : new Set(
          readAll<{ security_id: number }>(
            table,
            `SELECT DISTINCT security_id FROM ${table} WHERE security_id IN (${idList})`,
          ).rows.map((r) => r.security_id),
        );
    const covered = joinOnCik
      ? population.rows.filter((r) => r.cik && set.has(r.cik)).length
      : population.rows.filter((r) => set.has(r.security_id)).length;
    return { source: key, covered, total, pct: total ? (100 * covered) / total : 0 };
  });

  const byMetric: MetricCoverage[] = SCALAR_METRICS.map((metric) => {
    const rows = readAll<{ security_id: number; value: number | null; missing_fields_json: string | null }>(
      "derived_fundamentals",
      `SELECT security_id, ${metric} AS value, missing_fields_json FROM derived_fundamentals
        WHERE security_id IN (${idList})
          AND knowledge_date = (
            SELECT MAX(d2.knowledge_date) FROM derived_fundamentals d2
             WHERE d2.security_id = derived_fundamentals.security_id
          )`,
    ).rows;
    const bestPerSecurity = new Map<number, { value: number | null; missing_fields_json: string | null }>();
    for (const row of rows) {
      if (!bestPerSecurity.has(row.security_id)) bestPerSecurity.set(row.security_id, row);
    }
    let validCount = 0;
    const reasonCounts = new Map<string, number>();
    for (const row of bestPerSecurity.values()) {
      if (row.value !== null) {
        validCount += 1;
        continue;
      }
      let reason = "unknown";
      try {
        const missing = row.missing_fields_json ? JSON.parse(row.missing_fields_json) : null;
        if (missing && typeof missing === "object" && metric in missing) {
          reason = String(missing[metric]);
        }
      } catch {
        reason = "unknown";
      }
      reasonCounts.set(reason, (reasonCounts.get(reason) ?? 0) + 1);
    }
    return {
      metric,
      validCount,
      total,
      pct: total ? (100 * validCount) / total : 0,
      nullReasons: [...reasonCounts.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([reason, count]) => ({ reason, count })),
    };
  });

  const staleRows = readAll<{ security_id: number; latest_date: string | null }>(
    "prices",
    `SELECT security_id, MAX(date) AS latest_date FROM prices
      WHERE security_id IN (${idList}) GROUP BY security_id`,
  ).rows;
  const staleBySecurity = new Map(staleRows.map((r) => [r.security_id, r.latest_date]));
  const todayMs = Date.now();
  const buckets: Record<string, number> = { "0-1d": 0, "1-3d": 0, "3-7d": 0, "7d+": 0, "no data": 0 };
  for (const id of ids) {
    const latest = staleBySecurity.get(id);
    if (!latest) {
      buckets["no data"] += 1;
      continue;
    }
    const days = (todayMs - new Date(latest + "T00:00:00Z").getTime()) / 86_400_000;
    if (days <= 1) buckets["0-1d"] += 1;
    else if (days <= 3) buckets["1-3d"] += 1;
    else if (days <= 7) buckets["3-7d"] += 1;
    else buckets["7d+"] += 1;
  }
  const staleness = Object.entries(buckets).map(([bucket, count]) => ({ bucket, count }));

  const presentBySource = new Map<number, Set<string>>();
  for (const id of ids) presentBySource.set(id, new Set());
  for (const { key, table, joinOnCik } of COVERAGE_SOURCES) {
    if (joinOnCik) {
      const ciksPresent = new Set(
        readAll<{ cik: string }>(table, `SELECT DISTINCT cik FROM ${table} WHERE cik IS NOT NULL`).rows.map(
          (r) => r.cik,
        ),
      );
      for (const row of population.rows) {
        if (row.cik && ciksPresent.has(row.cik)) presentBySource.get(row.security_id)!.add(key);
      }
    } else {
      const idsPresent = new Set(
        readAll<{ security_id: number }>(
          table, `SELECT DISTINCT security_id FROM ${table} WHERE security_id IN (${idList})`,
        ).rows.map((r) => r.security_id),
      );
      for (const id of ids) {
        if (idsPresent.has(id)) presentBySource.get(id)!.add(key);
      }
    }
  }
  const worst: WorstCoverageRow[] = ids
    .map((id) => {
      const present = presentBySource.get(id) ?? new Set<string>();
      const missing = COVERAGE_SOURCES.map((s) => s.key).filter((k) => !present.has(k));
      return {
        security_id: id,
        symbol: bySecurity.get(id)?.symbol ?? null,
        sourcesPresent: present.size,
        sourcesTotal: COVERAGE_SOURCES.length,
        missing,
      };
    })
    .filter((row) => row.sourcesPresent < row.sourcesTotal)
    .sort((a, b) => a.sourcesPresent - b.sourcesPresent)
    .slice(0, 20);

  return { status: population.status, total, bySource, byMetric, staleness, worst };
}
