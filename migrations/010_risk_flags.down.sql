-- 010 down: drop the risk flags table and its view.
--
-- Risk flags are wholly derived from fundamentals, dilution signals, corporate
-- actions, insider transactions and filing text, all of which survive this
-- rollback. Recomputing them re-fetches the going-concern documents from EDGAR,
-- which is slow but loses nothing.

DROP VIEW IF EXISTS latest_risk_flags;
DROP INDEX IF EXISTS idx_risk_flags_unknown;
DROP INDEX IF EXISTS idx_risk_flags_code;
DROP INDEX IF EXISTS idx_risk_flags_security;
DROP TABLE IF EXISTS risk_flags;
