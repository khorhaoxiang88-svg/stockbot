-- 008 up: dilution signals.
--
-- NUMBERING NOTE: the F7 brief called this "migration 007", but 007
-- (amendment_link_nullable) was already applied during F6. Editing an applied
-- migration is forbidden, so this is 008. Contents are unchanged.
--
-- Dilution is the most protective signal in the system, which makes a false
-- positive expensive. The survey that motivated the gates: the fixture holds
-- 126,659 424B2 filings across 39 issuers, overwhelmingly bank structured notes
-- and medium-term notes. Counting 424B2 filings without classifying them would
-- disqualify JPMorgan and US Bancorp for issuing debt, while the genuinely
-- dilutive small caps file fewer than ten each.
--
-- So: no points are awarded until a filing is established as relating to
-- common-equity issuance. Ambiguous filings are recorded as 'unknown' and score
-- ZERO - unknown is not risk, and it is not a penalty.

CREATE TABLE IF NOT EXISTS dilution_signals (
    security_id         INTEGER NOT NULL REFERENCES securities (security_id),
    as_of_date          TEXT NOT NULL,          -- ET date the signal was computed for

    -- Split-adjusted year-over-year growth in common shares outstanding.
    -- NULL when it cannot be computed; never 0 as a stand-in.
    shares_yoy_growth   REAL,

    d1_capacity         REAL NOT NULL DEFAULT 0 CHECK (d1_capacity  BETWEEN 0 AND 4),
    d2_issuance         REAL NOT NULL DEFAULT 0 CHECK (d2_issuance  BETWEEN 0 AND 10),
    d3_structural       REAL NOT NULL DEFAULT 0 CHECK (d3_structural BETWEEN 0 AND 8),
    d4_realised         REAL NOT NULL DEFAULT 0 CHECK (d4_realised  BETWEEN 0 AND 12),

    dilution_score      REAL NOT NULL DEFAULT 0 CHECK (dilution_score BETWEEN 0 AND 30),
    is_disqualified     INTEGER NOT NULL DEFAULT 0 CHECK (is_disqualified IN (0, 1)),

    -- One entry per classified filing: accession, form, classification, reason,
    -- url, and which tier (if any) it contributed to. Every awarded point is
    -- traceable to a filing through this.
    evidence_json       TEXT,
    classification_notes TEXT,

    PRIMARY KEY (security_id, as_of_date),

    -- The frozen formula, asserted rather than trusted.
    CHECK (dilution_score = MIN(30, d1_capacity + d2_issuance + d3_structural + d4_realised)),
    -- Disqualification is exactly the stated threshold.
    CHECK (is_disqualified = (CASE WHEN dilution_score >= 22 THEN 1 ELSE 0 END)),
    -- 22 is unreachable from capacity alone: D1 + D3 maxes at 12, so
    -- disqualification always requires issuance or realised share growth.
    CHECK (is_disqualified = 0 OR (d2_issuance + d4_realised) > 0)
);

CREATE INDEX IF NOT EXISTS idx_dilution_security
    ON dilution_signals (security_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_dilution_disqualified
    ON dilution_signals (is_disqualified, as_of_date);
