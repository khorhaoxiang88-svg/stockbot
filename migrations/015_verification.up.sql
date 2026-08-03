-- 015 up: the Phase F exit-criteria harness.
--
-- Two tables carry this migration.
--
-- 1. verification_results. One row per check, per run. A check is not merely
--    "did it pass" -- PASS, FAIL and PENDING are three different statements,
--    and collapsing PENDING into FAIL (or into a silent PASS) would misrepresent
--    what is actually known. PENDING means the check could not be evaluated at
--    all right now (no live data exists to check, or -- check 5 -- verification
--    is a human task that has not happened yet), and it is never treated as
--    passing: "Phase S may not begin until every check passes" means PASS,
--    specifically, on all ten.
--
-- 2. filing_verifications. Check 5 requires 20 Form 4 filings hand-verified
--    against their live EDGAR source documents, at least 3 of them amendments.
--    That is fundamentally a human act -- comparing what this system stored
--    against what the actual filing document says -- and nothing in this
--    migration performs it. What is built is the RECORD: one row per filing a
--    human has actually opened and checked, with what was checked and whether
--    it matched. The table starts and stays empty until that verification
--    happens; check 5 counts real rows here, never invents them.
--
-- Neither table can be populated by recomputation the way F8's scores or F9's
-- risk flags can. That is the honest state of Phase F's exit gate today: nine
-- checks can be run and reported for real; the tenth is a checklist waiting on
-- a human, and the harness says so rather than quietly marking it done.

CREATE TABLE IF NOT EXISTS verification_results (
    run_id       TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    check_number INTEGER NOT NULL CHECK (check_number BETWEEN 1 AND 10),
    check_name   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'pending')),
    -- One-line human summary, e.g. "48 of 48 derived rows reproduced exactly".
    detail       TEXT NOT NULL,
    -- The full evidence backing `detail`: per-security results, arithmetic
    -- traces, counts by category -- whatever a reader would need to confirm
    -- the summary without re-running the check themselves.
    evidence_json TEXT NOT NULL,

    PRIMARY KEY (run_id, check_number)
);

CREATE INDEX IF NOT EXISTS idx_verification_results_run
    ON verification_results (run_id, check_number);

-- The most recent run's ten rows, for the /health page. Point-in-time queries
-- must NOT use this; they filter verification_results on run_id directly.
CREATE VIEW IF NOT EXISTS latest_verification_results AS
SELECT v.*
  FROM verification_results v
 WHERE v.run_id = (
     SELECT run_id FROM pipeline_runs
      WHERE stage = 'verification'
      ORDER BY started_at DESC LIMIT 1
 );

CREATE TABLE IF NOT EXISTS filing_verifications (
    -- No FK to insider_transactions: its primary key is (accession_no, line_no)
    -- and one accession can carry several lines, all backed by the SAME source
    -- document. Verification happens once per document, so accession_no alone
    -- is the natural key here; existence against insider_transactions is
    -- checked by the application, not by a constraint SQLite cannot express
    -- against a non-unique column.
    accession_no       TEXT PRIMARY KEY,
    security_id        INTEGER NOT NULL REFERENCES securities (security_id),
    is_amendment        INTEGER NOT NULL CHECK (is_amendment IN (0, 1)),
    -- Did every field this system stored for this filing match the actual
    -- EDGAR source document? Not "did the filing look plausible" -- a
    -- genuine field-by-field comparison.
    matches_source      INTEGER NOT NULL CHECK (matches_source IN (0, 1)),
    -- Which fields were actually compared, so a reader can tell a thorough
    -- verification from a superficial one.
    fields_checked_json TEXT NOT NULL,
    discrepancy_notes   TEXT,
    source_url          TEXT NOT NULL,
    verified_by         TEXT NOT NULL,
    verified_at         TEXT NOT NULL   -- UTC
);

CREATE INDEX IF NOT EXISTS idx_filing_verifications_amendment
    ON filing_verifications (is_amendment, matches_source);
