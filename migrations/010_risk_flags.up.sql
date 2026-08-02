-- 010 up: risk flags.
--
-- NUMBERING NOTE: the F9 brief called this "migration 009", but 009 (scores)
-- was already applied in F8. Editing an applied migration is forbidden by the
-- project's standing rules, so this is 010. The column list is exactly the one
-- the brief specified.
--
-- Three ideas carry this table.
--
-- 1. AN UNKNOWN IS A FLAG. A check that could not be performed writes a row
--    with is_unknown = 1 and says why. If unknowns were simply absent, an empty
--    panel would read as "nothing is wrong" when it actually means "nothing was
--    looked at". Silence must never imply safety, so the absence of a row means
--    the check ran and found nothing, and only that.
--
-- 2. EVERY DETECTED FLAG CITES ITS SOURCE. source_accession is NOT NULL for
--    every row that is not an unknown, enforced below. Most values are SEC
--    accession numbers resolvable in `filings`. One flag, recent_reverse_split,
--    comes from the price vendor's corporate-action ledger rather than from a
--    filing, and carries a documented "ledger:" reference that resolves to a
--    corporate_actions row. That is stated rather than disguised as an
--    accession that does not exist.
--
-- 3. INSIDER SELLING IS NOT BEARISH. Insiders sell for taxes, diversification,
--    house purchases and divorces. Presenting a sale as a risk signal would be
--    an interpretation this system has no evidence for, so severity 'context'
--    is the only non-unknown severity the constraint permits for it.

CREATE TABLE IF NOT EXISTS risk_flags (
    security_id     INTEGER NOT NULL REFERENCES securities (security_id),
    as_of_date      TEXT NOT NULL,          -- ET date the flag was computed for

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
                        'recent_insider_selling')),

    -- Four distinct states, kept apart on purpose:
    --   high/medium/low  a risk was DETECTED, graded
    --   none             the check RAN and detected nothing
    --   context          neutral information, must not be read as bearish
    --   unknown          the check COULD NOT RUN
    -- 'none' and 'unknown' are the pair that matters. Collapsing them would make
    -- "we looked and it is fine" indistinguishable from "we never looked", which
    -- is the exact failure this table exists to prevent.
    severity        TEXT NOT NULL CHECK (severity IN (
                        'high', 'medium', 'low', 'none', 'context', 'unknown')),

    -- Plain-language statement of what was measured, with the numbers in it, so
    -- the panel never has to compute anything to explain itself.
    evidence_text   TEXT NOT NULL,
    -- SEC accession, or a 'ledger:' reference into corporate_actions.
    source_accession TEXT,
    is_unknown      INTEGER NOT NULL DEFAULT 0 CHECK (is_unknown IN (0, 1)),

    PRIMARY KEY (security_id, as_of_date, flag_code),

    -- An unknown carries severity 'unknown', and a determined flag never does.
    CHECK ((is_unknown = 1 AND severity = 'unknown')
        OR (is_unknown = 0 AND severity <> 'unknown')),
    -- Every detected flag must cite something. Unknowns may not have a source,
    -- because the missing source is often the reason they are unknown.
    CHECK (is_unknown = 1 OR source_accession IS NOT NULL),
    -- Insider selling can only ever be context. Enforced here so no future code
    -- path can quietly promote it to a bearish severity.
    CHECK (flag_code <> 'recent_insider_selling' OR severity IN ('context', 'unknown'))
);

CREATE INDEX IF NOT EXISTS idx_risk_flags_security
    ON risk_flags (security_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_risk_flags_code
    ON risk_flags (flag_code, severity, as_of_date);
CREATE INDEX IF NOT EXISTS idx_risk_flags_unknown
    ON risk_flags (is_unknown, as_of_date);

-- The newest computed date per security, for the web panel. Point-in-time
-- queries must NOT use this; they filter risk_flags on as_of_date directly.
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
