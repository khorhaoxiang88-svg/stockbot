/**
 * The score breakdown on /security/[id] must render explanation_json in a form a
 * reader can check by hand, and must show the withhold reason -- never a zero --
 * for a security that could not be scored.
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

/** Value: three of four valid, so pe/pb/fcf_yield renormalise to 1/3 each. */
const RANKED_EXPLANATION = {
  security_id: 1,
  symbol: "ACME",
  name: "Acme Manufacturing Inc.",
  score_date: "2026-07-29",
  knowledge_cutoff: "2026-07-29T23:59:59Z",
  snapshot_id: "universe-2026-07-29-abcdef12",
  cohort_id: "SIC-D",
  cohort_label: "SIC division D - Manufacturing",
  cohort_basis: "SIC-derived. Not GICS; no GICS data exists in this system.",
  provenance: {},
  components: {
    value: {
      weight: 0.3,
      score: 60,
      gate: "at least 3 of 4 submetrics valid",
      detail: {
        component: "value",
        renormalised_share: 1,
        effective_weight_sum: 1,
        submetrics: [
          {
            metric: "pe",
            kind: "percentile",
            nominal_weight: 0.25,
            effective_weight: 1 / 3,
            valid: true,
            raw_value: 21.5,
            percentile: 90,
            value_used: 90,
            contribution: 30,
            lower_is_better: true,
            comparison: {
              market_population: "official universe snapshot: operating common stock",
              market_count: 16,
              market_percentile: 90,
              cohort_population: "SIC-D",
              cohort_count: 8,
              cohort_percentile: null,
              blend_weight_w: 0,
              knowledge_cutoff: "2026-07-29T23:59:59Z",
              snapshot_id: "universe-2026-07-29-abcdef12",
            },
          },
          {
            metric: "pb",
            kind: "percentile",
            nominal_weight: 0.25,
            effective_weight: 1 / 3,
            valid: true,
            raw_value: 3.1,
            percentile: 60,
            value_used: 60,
            contribution: 20,
            lower_is_better: true,
            comparison: {
              market_population: "official universe snapshot: operating common stock",
              market_count: 16,
              market_percentile: 60,
              cohort_population: "SIC-D",
              cohort_count: 8,
              cohort_percentile: null,
              blend_weight_w: 0,
              knowledge_cutoff: "2026-07-29T23:59:59Z",
              snapshot_id: "universe-2026-07-29-abcdef12",
            },
          },
          {
            metric: "ev_ebitda",
            kind: "percentile",
            nominal_weight: 0.25,
            effective_weight: 0,
            valid: false,
            reason: "not reported or invalid at the knowledge cutoff",
            raw_value: null,
            percentile: null,
            value_used: null,
            contribution: null,
            lower_is_better: true,
          },
          {
            metric: "fcf_yield",
            kind: "percentile",
            nominal_weight: 0.25,
            effective_weight: 1 / 3,
            valid: true,
            raw_value: 0.031,
            percentile: 30,
            value_used: 30,
            contribution: 10,
            lower_is_better: false,
            comparison: {
              market_population: "official universe snapshot: operating common stock",
              market_count: 16,
              market_percentile: 30,
              cohort_population: "SIC-D",
              cohort_count: 8,
              cohort_percentile: null,
              blend_weight_w: 0,
              knowledge_cutoff: "2026-07-29T23:59:59Z",
              snapshot_id: "universe-2026-07-29-abcdef12",
            },
          },
        ],
      },
    },
    quality: {
      weight: 0.3,
      score: 70,
      gate: "Piotroski fully computable from two consecutive complete fiscal years",
      piotroski: {
        complete: true,
        f_score: 7,
        max_f_score: 9,
        value_used: (7 / 9) * 100,
        formula: "F / 9 * 100, absolute (never percentile-ranked)",
        period_end: "2025-12-31",
        prior_period_end: "2024-12-31",
        reason: null,
        signals: [
          {
            signal: "roa_positive",
            test: "ROA > 0",
            passed: true,
            points: 1,
            concept_used: "NetIncomeLoss",
            accession: "0000000000-26-000001",
          },
          {
            signal: "cfo_positive",
            test: "CFO > 0",
            passed: false,
            points: 0,
            concept_used: "NetCashProvidedByUsedInOperatingActivities",
            accession: "0000000000-26-000001",
          },
        ],
      },
      detail: {
        component: "quality",
        renormalised_share: 0.6,
        effective_weight_sum: 1,
        submetrics: [
          {
            metric: "piotroski_f_score",
            kind: "absolute",
            nominal_weight: 0.4,
            effective_weight: 0.4,
            valid: true,
            raw_value: 7,
            percentile: null,
            value_used: (7 / 9) * 100,
            contribution: 0.4 * (7 / 9) * 100,
          },
          {
            metric: "roic",
            kind: "percentile",
            nominal_weight: 0.2,
            effective_weight: 0.6,
            valid: true,
            raw_value: 0.18,
            percentile: 65,
            value_used: 65,
            contribution: 39,
            comparison: {
              market_population: "official universe snapshot: operating common stock",
              market_count: 16,
              market_percentile: 65,
              cohort_population: "SIC-D",
              cohort_count: 12,
              cohort_percentile: 70,
              blend_weight_w: 0.24,
              knowledge_cutoff: "2026-07-29T23:59:59Z",
              snapshot_id: "universe-2026-07-29-abcdef12",
            },
          },
        ],
      },
    },
    momentum: {
      weight: 0.3,
      score: 50,
      gate: "at least 250 adjusted trading days",
      population: "whole operating universe, never the cohort",
      detail: {
        component: "momentum",
        renormalised_share: 1,
        effective_weight_sum: 1,
        submetrics: [
          {
            metric: "trend",
            kind: "absolute",
            nominal_weight: 0.05,
            effective_weight: 0.05,
            valid: true,
            raw_value: 100,
            percentile: null,
            value_used: 100,
            contribution: 5,
          },
        ],
      },
    },
  },
  insider_bonus: {
    value: 2.5,
    formula: "min(10, B1 + B2 + B3 + B4)",
    coverage: {
      complete: true,
      reason: "every Form 4 in the ingest window was ingested",
      note: "An observed zero is ranked; unknown coverage withholds ranking.",
    },
    qualifying_definition: "Table I, transaction code P, not superseded",
    qualifying_purchases: 4,
    b1_cluster: {
      formula: "4 * min(1, max(0, N - 2) / 2) * mean(q_i)",
      value: 2,
      distinct_insiders_N: 3,
      window_days: 90,
    },
    b2_executive: { formula: "2 * max(c * d) over CEO/CFO purchases", value: 0.5 },
    b3_size: {
      formula: "B3 = 2 * pct(S, {S > 0}) / 100",
      value: 0,
      S: 0.00001234,
      population_count: 4,
    },
    b4_conviction: {
      formula: "2 * max(c * d) where purchased / prior_holdings > 0.25",
      value: 0,
    },
    sum_before_cap: 2.5,
  },
  dilution_penalty: { dilution_score: 4 },
  composite: {
    formula:
      "0.30*Value + 0.30*Quality + 0.30*Momentum + InsiderBonus - DilutionPenalty, clamped to [0, 100]",
    terms: [
      { term: "0.30 * Value", weight: 0.3, component: 60, contribution: 18 },
      { term: "0.30 * Quality", weight: 0.3, component: 70, contribution: 21 },
      { term: "0.30 * Momentum", weight: 0.3, component: 50, contribution: 15 },
      { term: "+ InsiderBonus", weight: 1, component: 2.5, contribution: 2.5 },
      { term: "- DilutionPenalty", weight: -1, component: 4, contribution: -4 },
    ],
    unclamped: 52.5,
    clamped: 52.5,
  },
  rankable: true,
  withhold_reason: null,
  altman_z_note: "Altman Z'' is deliberately absent from the composite.",
  winsorisation_note: "The caps are applied in F5 and are not reapplied here.",
};

const WITHHELD_EXPLANATION = {
  security_id: 2,
  symbol: "BANKCO",
  name: "Bankco Financial Group",
  score_date: "2026-07-29",
  snapshot_id: "universe-2026-07-29-abcdef12",
  cohort_id: "SIC-H",
  cohort_label: "SIC division H - Finance, Insurance and Real Estate",
  cohort_basis: "SIC-derived. Not GICS; no GICS data exists in this system.",
  universe_status: "excluded",
  rankable: false,
  withhold_reason:
    "model not supported: SIC division H (finance, insurance, real estate) carries model_applicable = 0 from F5",
};

async function buildDatabase() {
  const { default: Database } = await import("better-sqlite3");
  const db = new Database(dbPath);
  for (const file of fs
    .readdirSync(MIGRATIONS)
    .filter((name) => name.endsWith(".up.sql"))
    .sort()) {
    db.exec(fs.readFileSync(path.join(MIGRATIONS, file), "utf-8"));
  }

  const insertSecurity = db.prepare(
    `INSERT INTO securities (security_id, cik, share_class, name, security_type,
                             classification_confidence, classification_source, sic_code,
                             first_seen, last_seen, is_active, delisted_date)
     VALUES (?, ?, NULL, ?, ?, 'high', 'test', ?, '2026-07-29T00:00:00Z',
             '2026-07-29T00:00:00Z', 1, NULL)`,
  );
  insertSecurity.run(1, "0000000001", "Acme Manufacturing Inc.", "common_stock", "3571");
  insertSecurity.run(2, "0000000002", "Bankco Financial Group", "common_stock", "6021");

  const insertListing = db.prepare(
    `INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary)
     VALUES (?, ?, 'NYSE', '2026-07-29', NULL, 1)`,
  );
  insertListing.run(1, "ACME");
  insertListing.run(2, "BANKCO");

  const insertScore = db.prepare(
    `INSERT INTO scores (security_id, score_date, strategy_version, config_hash,
                         mapping_version, price_dataset_version, price_snapshot_hash,
                         value_score, quality_score, momentum_score, insider_bonus,
                         dilution_penalty, composite_score, "rank", cohort_id, rankable,
                         withhold_reason, explanation_json)
     VALUES (?, '2026-07-29', 1, ?, '1', 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  insertScore.run(
    1, "a".repeat(64), "b".repeat(64), 60, 70, 50, 2.5, 4, 52.5, 1, "SIC-D", 1, null,
    JSON.stringify(RANKED_EXPLANATION),
  );
  insertScore.run(
    2, "a".repeat(64), null, null, null, null, null, 0, null, null, "SIC-H", 0,
    WITHHELD_EXPLANATION.withhold_reason, JSON.stringify(WITHHELD_EXPLANATION),
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
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-score-"));
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

describe("score breakdown", () => {
  it("shows the composite, its rank and the arithmetic that produced it", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Composite score");
    expect(html).toContain("52.5000 of 100");
    expect(html).toContain("rank 1 of 1");
    expect(html).toContain("0.30*Value + 0.30*Quality + 0.30*Momentum");
    // Every term of the sum is on the page, signed.
    expect(html).toContain("+18.0000");
    expect(html).toContain("+21.0000");
    expect(html).toContain("+15.0000");
    expect(html).toContain("+2.5000");
    expect(html).toContain("−4.0000");
    expect(html).toContain("Total before clamping");
  });

  it("renders every submetric with nominal and effective weight", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("P/E");
    expect(html).toContain("EV/EBITDA");
    expect(html).toContain("Nominal w");
    expect(html).toContain("Effective w");
    // The renormalised 1/3 weight is displayed to four places.
    expect(html).toContain("0.3333");
    // The invalid submetric shows its reason and a zero effective weight.
    expect(html).toContain("not reported or invalid at the knowledge cutoff");
    expect(html).toContain("lower is better; percentile inverted after ranking");
  });

  it("names the comparison population, its count and the blend weight", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("market: 16 valid");
    expect(html).toContain("cohort SIC-D");
    expect(html).toContain("Blend w");
    expect(html).toContain("SIC division D - Manufacturing");
    // The cohort basis must never be described as GICS.
    expect(html).toContain("Not GICS");
    expect(html).not.toContain("GICS sector");
  });

  it("shows the Piotroski signals and its fixed 0.40 share", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("Piotroski F-score 7 of 9");
    expect(html).toContain("F / 9 * 100, absolute (never percentile-ranked)");
    expect(html).toContain("ROA &gt; 0");
    expect(html).toContain("2024-12-31");
    expect(html).toContain("Its 0.40 share never changes");
  });

  it("shows each insider sub-bonus and the coverage decision", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("B1 cluster");
    expect(html).toContain("B2 executive");
    expect(html).toContain("B3 size");
    expect(html).toContain("B4 conviction");
    expect(html).toContain("4 * min(1, max(0, N - 2) / 2) * mean(q_i)");
    expect(html).toContain("Form 4 coverage: complete and current");
    expect(html).toContain("N = 3 distinct insiders");
    expect(html).toContain("Sum before the cap");
  });

  it("shows the withhold reason instead of a score, and never a zero", async () => {
    const html = await renderSecurityPage("2");
    expect(html).toContain("Not ranked — no composite score");
    expect(html).toContain("model not supported");
    expect(html).toContain("SIC division H");
    // No composite badge at all for a withheld security.
    expect(html).not.toContain("of 100");
    expect(html).not.toContain("0.0000 of 100");
    expect(html).toContain("Zero is a real score");
  });

  it("records the provenance stamps that make the score reproducible", async () => {
    const html = await renderSecurityPage("1");
    expect(html).toContain("config hash");
    expect(html).toContain("price dataset version");
    expect(html).toContain("price snapshot");
    expect(html).toContain("universe snapshot");
    expect(html).toContain("universe-2026-07-29-abcdef12");
    expect(html).toContain("knowledge cutoff");
    expect(html).toContain("Altman Z");
  });
});
