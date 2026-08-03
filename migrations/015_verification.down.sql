-- 015 down: drop the verification harness tables.
--
-- verification_results is wholly derived -- every check recomputes from data
-- that survives this rollback, so it is safe to drop. filing_verifications is
-- NOT derived: it is the only record of real human verification work against
-- live EDGAR documents, and dropping it destroys that work permanently. This
-- file exists for undoing a bad deployment, not for routine use; back up
-- filing_verifications first if any rows exist.

DROP VIEW IF EXISTS latest_verification_results;
DROP INDEX IF EXISTS idx_filing_verifications_amendment;
DROP TABLE IF EXISTS filing_verifications;
DROP INDEX IF EXISTS idx_verification_results_run;
DROP TABLE IF EXISTS verification_results;
