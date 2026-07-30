-- 006 up: insider transactions from Form 4.
--
-- This is the system's primary claimed edge, so the parsing rules are encoded
-- as constraints rather than left to convention.
--
--   * Table I (non-derivative) and Table II (derivative) are structurally
--     different instruments. Both are stored, table_type distinguishes them,
--     and only Table I is ever scored.
--   * transaction_code is stored verbatim. P is an open-market purchase, S a
--     sale, A a grant, M an option exercise. Only P is ever scored as a
--     purchase; a grant is not a vote of confidence bought with cash.
--   * plan_status is never guessed. The Rule 10b5-1 checkbox (aff10b5One) is a
--     recent addition to Form 4 and is simply absent from older filings. When
--     it cannot be determined the value is 'unknown', never a default of
--     'discretionary' or 'confirmed_10b5_1'.
--   * An amendment SUPERSEDES: it sets superseded_by_accession on the original
--     row, which is RETAINED. Reads filter superseded rows. Nothing is deleted
--     and nothing is double-counted.

CREATE TABLE IF NOT EXISTS insider_transactions (
    accession_no            TEXT NOT NULL,
    line_no                 INTEGER NOT NULL,
    security_id             INTEGER REFERENCES securities (security_id),
    insider_cik             TEXT,
    insider_name            TEXT,
    role_officer            INTEGER NOT NULL DEFAULT 0 CHECK (role_officer IN (0, 1)),
    role_director           INTEGER NOT NULL DEFAULT 0 CHECK (role_director IN (0, 1)),
    role_ten_percent        INTEGER NOT NULL DEFAULT 0 CHECK (role_ten_percent IN (0, 1)),
    officer_title           TEXT,
    transaction_date        TEXT,              -- ET trading date as reported
    filed_date              TEXT,
    accepted_at             TEXT,              -- UTC
    -- 'I' = Table I non-derivative, 'II' = Table II derivative.
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

    -- 'unknown' must carry 'absent', and a determined status must not.
    CHECK (
        (plan_status = 'unknown' AND plan_status_source = 'absent')
        OR (plan_status <> 'unknown' AND plan_status_source IN ('checkbox', 'footnote'))
    ),
    -- An amendment must say what it amends.
    CHECK (is_amendment = 0 OR amends_accession IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_insider_security
    ON insider_transactions (security_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_insider_code
    ON insider_transactions (transaction_code, table_type);
CREATE INDEX IF NOT EXISTS idx_insider_superseded
    ON insider_transactions (superseded_by_accession);
CREATE INDEX IF NOT EXISTS idx_insider_owner
    ON insider_transactions (insider_cik, transaction_date);

-- The one definition of a scored purchase, so no query can invent its own.
-- Table I only, code P only, superseded rows excluded.
CREATE VIEW IF NOT EXISTS scored_insider_purchases AS
SELECT *
  FROM insider_transactions
 WHERE table_type = 'I'
   AND transaction_code = 'P'
   AND superseded_by_accession IS NULL;

-- Every currently-effective row, superseded ones removed.
CREATE VIEW IF NOT EXISTS effective_insider_transactions AS
SELECT *
  FROM insider_transactions
 WHERE superseded_by_accession IS NULL;
