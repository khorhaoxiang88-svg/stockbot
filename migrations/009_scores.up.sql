-- 009 up: composite scores.
--
-- NUMBERING NOTE: the F8 brief called this "migration 008", but 008
-- (dilution_signals) was already applied during F7. Editing an applied
-- migration is forbidden by the project's standing rules, so this is 009.
-- The column list is exactly the one the brief specified.
--
-- The point of this table is REPRODUCIBILITY. A composite score is a number
-- produced by roughly forty intermediate decisions, and a number no one can
-- re-derive is not evidence. So every row carries:
--
--   * the four provenance stamps that pin the inputs -- strategy_version,
--     config_hash, mapping_version, price_dataset_version + price_snapshot_hash;
--   * explanation_json, which holds every submetric's nominal weight, validity,
--     EFFECTIVE weight after renormalisation, comparison population name and
--     count, cohort blend weight, raw value, percentile and final contribution,
--     plus every insider sub-bonus calculation.
--
-- Given explanation_json alone, composite_score can be recomputed by hand.
-- The CHECK constraints below assert the parts of that which SQL can assert.
--
-- WITHHELD IS NOT ZERO. A security that cannot be scored stores NULL scores and
-- a withhold_reason. It never stores 0, because 0 is a real score meaning "worst
-- in the universe" and would rank a security we know nothing about.

CREATE TABLE IF NOT EXISTS scores (
    security_id           INTEGER NOT NULL REFERENCES securities (security_id),
    score_date            TEXT NOT NULL,      -- ET trading date the score is for
    strategy_version      INTEGER NOT NULL,
    config_hash           TEXT NOT NULL,      -- sha256 of config.frozen.json
    mapping_version       TEXT NOT NULL,      -- concept mapping version behind fundamentals
    price_dataset_version INTEGER,            -- price_dataset_versions.dataset_version
    price_snapshot_hash   TEXT,               -- sha256 of the adjusted bars used

    ------------------------------------------------------------------ components
    -- 0-100 each. NULL means the component's gate failed; it never means zero.
    value_score      REAL CHECK (value_score    IS NULL OR value_score    BETWEEN 0 AND 100),
    quality_score    REAL CHECK (quality_score  IS NULL OR quality_score  BETWEEN 0 AND 100),
    momentum_score   REAL CHECK (momentum_score IS NULL OR momentum_score BETWEEN 0 AND 100),
    -- Additive, 0-10. NULL means insider coverage is unknown, which withholds
    -- ranking. An observed zero is 0.0, not NULL.
    insider_bonus    REAL CHECK (insider_bonus IS NULL OR insider_bonus BETWEEN 0 AND 10),
    -- The F7 dilution score, 0-30, subtracted as specified.
    dilution_penalty REAL NOT NULL DEFAULT 0 CHECK (dilution_penalty BETWEEN 0 AND 30),

    composite_score  REAL CHECK (composite_score IS NULL OR composite_score BETWEEN 0 AND 100),
    "rank"           INTEGER,
    cohort_id        TEXT,                    -- SIC-derived. Never GICS.

    rankable         INTEGER NOT NULL DEFAULT 0 CHECK (rankable IN (0, 1)),
    withhold_reason  TEXT,
    explanation_json TEXT NOT NULL,

    PRIMARY KEY (security_id, score_date, strategy_version),

    -- A withheld security must say why, and must carry no score and no rank.
    CHECK (rankable = 1 OR (withhold_reason IS NOT NULL
                            AND composite_score IS NULL
                            AND "rank" IS NULL)),
    -- A ranked security must have every component and no withhold reason. This
    -- is the "no component weight is ever redistributed" rule at the storage
    -- layer: a missing component cannot be quietly absorbed by the others.
    CHECK (rankable = 0 OR (value_score IS NOT NULL
                            AND quality_score IS NOT NULL
                            AND momentum_score IS NOT NULL
                            AND insider_bonus IS NOT NULL
                            AND composite_score IS NOT NULL
                            AND "rank" IS NOT NULL
                            AND withhold_reason IS NULL)),
    -- The frozen composite formula, asserted rather than trusted. Written with
    -- MIN/MAX so it is the clamp to [0, 100] that SQLite actually evaluates.
    CHECK (rankable = 0 OR ABS(composite_score - MAX(0, MIN(100,
              0.30 * value_score
            + 0.30 * quality_score
            + 0.30 * momentum_score
            + insider_bonus
            - dilution_penalty))) < 0.000001)
);

CREATE INDEX IF NOT EXISTS idx_scores_ranking
    ON scores (score_date, strategy_version, rankable, "rank");
CREATE INDEX IF NOT EXISTS idx_scores_security
    ON scores (security_id, score_date DESC);
CREATE INDEX IF NOT EXISTS idx_scores_cohort
    ON scores (cohort_id, score_date);

-- The most recent scoring date per strategy version, for the web app. Point-in-
-- time queries must NOT use this; they filter scores on score_date directly.
CREATE VIEW IF NOT EXISTS latest_scores AS
SELECT s.*
  FROM scores s
  JOIN (
      SELECT strategy_version, MAX(score_date) AS score_date
        FROM scores
       GROUP BY strategy_version
  ) newest
    ON newest.strategy_version = s.strategy_version
   AND newest.score_date = s.score_date;
