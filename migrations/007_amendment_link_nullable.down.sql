-- 007 down: restore the 006 constraint set.
--
-- Rows whose amends_accession is NULL would violate the restored CHECK, so the
-- rollback fails loudly rather than inventing a value for them. Clear those
-- rows first if the rollback is genuinely wanted.

PRAGMA foreign_keys = OFF;

DROP VIEW IF EXISTS scored_insider_purchases;
DROP VIEW IF EXISTS effective_insider_transactions;

CREATE TABLE insider_transactions_old (
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
    CHECK (
        (plan_status = 'unknown' AND plan_status_source = 'absent')
        OR (plan_status <> 'unknown' AND plan_status_source IN ('checkbox', 'footnote'))
    ),
    CHECK (is_amendment = 0 OR amends_accession IS NOT NULL)
);

INSERT INTO insider_transactions_old SELECT * FROM insider_transactions;

DROP TABLE insider_transactions;
ALTER TABLE insider_transactions_old RENAME TO insider_transactions;

CREATE INDEX IF NOT EXISTS idx_insider_security
    ON insider_transactions (security_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_insider_code
    ON insider_transactions (transaction_code, table_type);
CREATE INDEX IF NOT EXISTS idx_insider_superseded
    ON insider_transactions (superseded_by_accession);
CREATE INDEX IF NOT EXISTS idx_insider_owner
    ON insider_transactions (insider_cik, transaction_date);

CREATE VIEW scored_insider_purchases AS
SELECT * FROM insider_transactions
 WHERE table_type = 'I' AND transaction_code = 'P' AND superseded_by_accession IS NULL;

CREATE VIEW effective_insider_transactions AS
SELECT * FROM insider_transactions WHERE superseded_by_accession IS NULL;

PRAGMA foreign_keys = ON;
