-- 007 up: allow amends_accession to be NULL when the original is unknown.
--
-- Migration 006 carried this CHECK:
--
--     CHECK (is_amendment = 0 OR amends_accession IS NOT NULL)
--
-- That encodes an assumption that turned out to be false. A Form 4/A carries
-- dateOfOriginalSubmission but NOT the original's accession number, so the link
-- has to be derived from (security, insider, period). When the original is
-- outside the ingested window, or was never matched, there is no honest value
-- to store. The constraint forced the ingest to write a self-reference as a
-- placeholder, which read as "this amendment amends itself" for 136 rows.
--
-- NULL is the truthful encoding for "amends a filing we have not identified".
-- The supersede behaviour is unchanged: when the original IS identified, it is
-- retained and marked via superseded_by_accession.
--
-- SQLite cannot drop a CHECK, so the table is rebuilt and the rows copied.

PRAGMA foreign_keys = OFF;

-- Views depend on the table, so they must go first or DROP TABLE fails.
DROP VIEW IF EXISTS scored_insider_purchases;
DROP VIEW IF EXISTS effective_insider_transactions;

CREATE TABLE insider_transactions_new (
    accession_no            TEXT NOT NULL,
    line_no                 INTEGER NOT NULL,
    security_id             INTEGER REFERENCES securities (security_id),
    insider_cik             TEXT,
    insider_name            TEXT,
    role_officer            INTEGER NOT NULL DEFAULT 0 CHECK (role_officer IN (0, 1)),
    role_director           INTEGER NOT NULL DEFAULT 0 CHECK (role_director IN (0, 1)),
    role_ten_percent        INTEGER NOT NULL DEFAULT 0 CHECK (role_ten_percent IN (0, 1)),
    officer_title           TEXT,
    transaction_date        TEXT,
    filed_date              TEXT,
    accepted_at             TEXT,
    table_type              TEXT NOT NULL CHECK (table_type IN ('I', 'II')),
    transaction_code        TEXT,
    plan_status             TEXT NOT NULL DEFAULT 'unknown'
                                 CHECK (plan_status IN ('discretionary', 'confirmed_10b5_1', 'unknown')),
    plan_status_source      TEXT NOT NULL DEFAULT 'absent'
                                 CHECK (plan_status_source IN ('checkbox', 'footnote', 'absent')),
    shares                  REAL,
    price_per_share         REAL,
    total_value             REAL,
    shares_owned_after      REAL,
    is_amendment            INTEGER NOT NULL DEFAULT 0 CHECK (is_amendment IN (0, 1)),
    amends_accession        TEXT,
    superseded_by_accession TEXT,
    payload_id              TEXT REFERENCES raw_payloads (payload_id),

    PRIMARY KEY (accession_no, line_no),

    -- Retained: 'unknown' must carry 'absent', and a determined status must not.
    CHECK (
        (plan_status = 'unknown' AND plan_status_source = 'absent')
        OR (plan_status <> 'unknown' AND plan_status_source IN ('checkbox', 'footnote'))
    ),
    -- Retained and tightened: an amendment must never point at itself.
    CHECK (amends_accession IS NULL OR amends_accession <> accession_no)
);

INSERT INTO insider_transactions_new
SELECT accession_no, line_no, security_id, insider_cik, insider_name, role_officer,
       role_director, role_ten_percent, officer_title, transaction_date, filed_date,
       accepted_at, table_type, transaction_code, plan_status, plan_status_source,
       shares, price_per_share, total_value, shares_owned_after, is_amendment,
       -- Drop the self-referencing placeholders.
       CASE WHEN amends_accession = accession_no THEN NULL ELSE amends_accession END,
       superseded_by_accession, payload_id
  FROM insider_transactions;

DROP TABLE insider_transactions;
ALTER TABLE insider_transactions_new RENAME TO insider_transactions;

CREATE INDEX IF NOT EXISTS idx_insider_security
    ON insider_transactions (security_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_insider_code
    ON insider_transactions (transaction_code, table_type);
CREATE INDEX IF NOT EXISTS idx_insider_superseded
    ON insider_transactions (superseded_by_accession);
CREATE INDEX IF NOT EXISTS idx_insider_owner
    ON insider_transactions (insider_cik, transaction_date);

-- Recreate the views unchanged.
CREATE VIEW scored_insider_purchases AS
SELECT * FROM insider_transactions
 WHERE table_type = 'I' AND transaction_code = 'P' AND superseded_by_accession IS NULL;

CREATE VIEW effective_insider_transactions AS
SELECT * FROM insider_transactions WHERE superseded_by_accession IS NULL;

PRAGMA foreign_keys = ON;
