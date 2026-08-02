-- 012 up: add the 'overdue_issuer_filing' risk flag code.
--
-- WHY A MIGRATION FOR ONE ENUM VALUE. F10 draws a distinction F9 did not have a
-- flag for:
--
--   "SEC submissions/XBRL: the latest SCHEDULED ingestion must have succeeded.
--    An older company filing is NOT automatically stale merely because the
--    company has not filed recently. If a company filing appears overdue under
--    its applicable filing schedule, surface that as a risk/unknown flag."
--
-- Those are two different failures wearing the same symptom. Our pipeline being
-- behind is stale_or_incomplete_data and blocks candidate selection. A company
-- being behind on its own filings is the company's problem, is genuine
-- information about the company, and must NOT block selection or masquerade as
-- a pipeline fault. It needs its own code, and flag_code is a CHECK constraint,
-- which SQLite cannot alter in place. So the table is rebuilt and the rows
-- copied, the same way migration 007 rebuilt insider_transactions.
--
-- Nothing else about the table changes. Every existing row is preserved.

PRAGMA foreign_keys = OFF;

DROP VIEW IF EXISTS latest_risk_flags;

CREATE TABLE risk_flags_new (
    security_id     INTEGER NOT NULL REFERENCES securities (security_id),
    as_of_date      TEXT NOT NULL,

    flag_code       TEXT NOT NULL CHECK (flag_code IN (
                        'negative_operating_cash_flow',
                        'negative_free_cash_flow',
                        'high_leverage',
                        'low_interest_coverage',
                        'rapid_share_growth',
                        'shelf_capacity',
                        'active_issuance',
                        'atm_or_convertible',
                        'recent_reverse_split',
                        'altman_distress',
                        'going_concern',
                        'stale_or_incomplete_data',
                        'overdue_issuer_filing',
                        'recent_insider_selling')),

    severity        TEXT NOT NULL CHECK (severity IN (
                        'high', 'medium', 'low', 'none', 'context', 'unknown')),

    evidence_text   TEXT NOT NULL,
    source_accession TEXT,
    is_unknown      INTEGER NOT NULL DEFAULT 0 CHECK (is_unknown IN (0, 1)),

    PRIMARY KEY (security_id, as_of_date, flag_code),

    CHECK ((is_unknown = 1 AND severity = 'unknown')
        OR (is_unknown = 0 AND severity <> 'unknown')),
    CHECK (is_unknown = 1 OR source_accession IS NOT NULL),
    CHECK (flag_code <> 'recent_insider_selling' OR severity IN ('context', 'unknown'))
);

INSERT INTO risk_flags_new
    (security_id, as_of_date, flag_code, severity, evidence_text,
     source_accession, is_unknown)
SELECT security_id, as_of_date, flag_code, severity, evidence_text,
       source_accession, is_unknown
  FROM risk_flags;

DROP TABLE risk_flags;
ALTER TABLE risk_flags_new RENAME TO risk_flags;

CREATE INDEX IF NOT EXISTS idx_risk_flags_security
    ON risk_flags (security_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_risk_flags_code
    ON risk_flags (flag_code, severity, as_of_date);
CREATE INDEX IF NOT EXISTS idx_risk_flags_unknown
    ON risk_flags (is_unknown, as_of_date);

CREATE VIEW IF NOT EXISTS latest_risk_flags AS
SELECT r.*
  FROM risk_flags r
  JOIN (
      SELECT security_id, MAX(as_of_date) AS as_of_date
        FROM risk_flags
       GROUP BY security_id
  ) newest
    ON newest.security_id = r.security_id
   AND newest.as_of_date = r.as_of_date;

PRAGMA foreign_keys = ON;
