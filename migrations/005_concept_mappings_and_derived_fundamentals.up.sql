-- 005 up: concept mappings and derived fundamentals.
--
-- Two ideas carry this migration.
--
-- 1. PROVENANCE PER NUMBER. Issuers tag the same economic quantity with
--    different XBRL concepts. Every derived value therefore records which
--    concept actually produced it and from which accession, so any number can
--    be traced back to a specific tag in a specific filing.
--
-- 2. knowledge_date. An amendment creates a NEW row; it never overwrites the
--    old one. Without that, "what did we know on 2024-08-01" is unanswerable,
--    and a backtest silently uses restated figures the strategy could not have
--    seen. knowledge_date is part of the primary key for exactly this reason.
--
-- Missing inputs produce NULL and are listed in missing_fields_json. Never 0.

CREATE TABLE IF NOT EXISTS concept_mappings (
    metric_name     TEXT NOT NULL,
    taxonomy        TEXT NOT NULL,
    concept         TEXT NOT NULL,
    priority        INTEGER NOT NULL,      -- 1 = try first
    mapping_version TEXT NOT NULL,
    valid_from      TEXT,                  -- UTC, NULL = always
    valid_to        TEXT,                  -- UTC, NULL = current
    notes           TEXT,
    PRIMARY KEY (metric_name, taxonomy, concept, mapping_version),
    CHECK (priority >= 1)
);

CREATE INDEX IF NOT EXISTS idx_concept_mappings_lookup
    ON concept_mappings (mapping_version, metric_name, priority);

CREATE TABLE IF NOT EXISTS derived_fundamentals (
    security_id     INTEGER NOT NULL REFERENCES securities (security_id),
    period_end      TEXT NOT NULL,         -- fiscal period end, as reported
    -- When this fact set became knowable: the latest acceptance timestamp among
    -- the facts used. An amendment lands later and creates another row.
    knowledge_date  TEXT NOT NULL,         -- UTC
    fact_set_hash   TEXT NOT NULL,         -- hash of the exact facts used
    mapping_version TEXT NOT NULL,

    ----------------------------------------------------------------- valuation
    pe                              REAL,
    pe_concept_used                 TEXT,
    pe_accession                    TEXT,
    pb                              REAL,
    pb_concept_used                 TEXT,
    pb_accession                    TEXT,
    ev_ebitda                       REAL,
    ev_ebitda_concept_used          TEXT,
    ev_ebitda_accession             TEXT,
    fcf_yield                       REAL,
    fcf_yield_concept_used          TEXT,
    fcf_yield_accession             TEXT,

    ------------------------------------------------------------------- quality
    roic                            REAL,
    roic_concept_used               TEXT,
    roic_accession                  TEXT,
    interest_coverage               REAL,
    interest_coverage_concept_used  TEXT,
    interest_coverage_accession     TEXT,
    debt_ebitda                     REAL,
    debt_ebitda_concept_used        TEXT,
    debt_ebitda_accession           TEXT,
    current_ratio                   REAL,
    current_ratio_concept_used      TEXT,
    current_ratio_accession         TEXT,
    gross_margin                    REAL,
    gross_margin_concept_used       TEXT,
    gross_margin_accession          TEXT,
    revenue_growth_yoy              REAL,
    revenue_growth_yoy_concept_used TEXT,
    revenue_growth_yoy_accession    TEXT,
    shares_outstanding              REAL,
    shares_outstanding_concept_used TEXT,
    shares_outstanding_accession    TEXT,

    ------------------------------------------------------- Piotroski, 9 inputs
    piotroski_roa_positive                       INTEGER,
    piotroski_roa_positive_concept_used          TEXT,
    piotroski_roa_positive_accession             TEXT,
    piotroski_cfo_positive                       INTEGER,
    piotroski_cfo_positive_concept_used          TEXT,
    piotroski_cfo_positive_accession             TEXT,
    piotroski_roa_improved                       INTEGER,
    piotroski_roa_improved_concept_used          TEXT,
    piotroski_roa_improved_accession             TEXT,
    piotroski_accruals                           INTEGER,
    piotroski_accruals_concept_used              TEXT,
    piotroski_accruals_accession                 TEXT,
    piotroski_leverage_decreased                 INTEGER,
    piotroski_leverage_decreased_concept_used    TEXT,
    piotroski_leverage_decreased_accession       TEXT,
    piotroski_current_ratio_improved             INTEGER,
    piotroski_current_ratio_improved_concept_used TEXT,
    piotroski_current_ratio_improved_accession   TEXT,
    piotroski_no_new_shares                      INTEGER,
    piotroski_no_new_shares_concept_used         TEXT,
    piotroski_no_new_shares_accession            TEXT,
    piotroski_gross_margin_improved              INTEGER,
    piotroski_gross_margin_improved_concept_used TEXT,
    piotroski_gross_margin_improved_accession    TEXT,
    piotroski_asset_turnover_improved            INTEGER,
    piotroski_asset_turnover_improved_concept_used TEXT,
    piotroski_asset_turnover_improved_accession  TEXT,

    --------------------------------------------------------------- market cap
    -- Multi-class issuers are genuinely ambiguous: a single class's share count
    -- times that class's price is not the whole company. The inputs and a
    -- confidence state are stored so an ambiguous figure is never presented as
    -- exact.
    market_cap                  REAL,
    market_cap_confidence       TEXT CHECK (market_cap_confidence IN ('high', 'medium', 'low')),
    market_cap_shares_used      REAL,
    market_cap_price_used       REAL,
    market_cap_price_date       TEXT,      -- ET trading date of the price used
    market_cap_concept_used     TEXT,
    market_cap_accession        TEXT,
    market_cap_ambiguity_reason TEXT,

    -------------------------------------------------------------- bookkeeping
    inputs_complete     INTEGER NOT NULL DEFAULT 0 CHECK (inputs_complete IN (0, 1)),
    missing_fields_json TEXT,
    -- Banks, insurers, other financials and REITs. EV/EBITDA and current ratio
    -- have no valid economic interpretation for them, so they are never ranked.
    model_applicable    INTEGER NOT NULL DEFAULT 1 CHECK (model_applicable IN (0, 1)),
    computed_at         TEXT NOT NULL,     -- UTC

    PRIMARY KEY (security_id, period_end, knowledge_date)
);

CREATE INDEX IF NOT EXISTS idx_derived_fundamentals_security
    ON derived_fundamentals (security_id, period_end DESC, knowledge_date DESC);
CREATE INDEX IF NOT EXISTS idx_derived_fundamentals_applicable
    ON derived_fundamentals (model_applicable, period_end);

-- The latest knowledge state per (security, period). Point-in-time queries must
-- NOT use this view; they filter derived_fundamentals by knowledge_date instead.
CREATE VIEW IF NOT EXISTS latest_fundamentals AS
SELECT d.*
  FROM derived_fundamentals d
  JOIN (
      SELECT security_id, period_end, MAX(knowledge_date) AS knowledge_date
        FROM derived_fundamentals
       GROUP BY security_id, period_end
  ) newest
    ON newest.security_id = d.security_id
   AND newest.period_end = d.period_end
   AND newest.knowledge_date = d.knowledge_date;
