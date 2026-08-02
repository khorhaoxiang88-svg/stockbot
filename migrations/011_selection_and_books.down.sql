-- 011 down: drop the selection tables, the books and positions.
--
-- The triggers must go before the table, or DROP TABLE fires them.
--
-- Unlike scores and risk flags, research candidates are NOT wholly derivable
-- from what survives this rollback: a candidate records what was decided with
-- the evidence available at one instant, and re-running the selection later
-- would reach that decision using evidence that arrived afterwards. Rolling
-- back therefore loses the audit trail permanently. That is the honest
-- consequence and the reason a down migration exists at all: it is for undoing
-- a bad deployment, not for routine use.

DROP VIEW IF EXISTS latest_selection;

DROP INDEX IF EXISTS idx_positions_cooldown;
DROP INDEX IF EXISTS idx_positions_book;
DROP INDEX IF EXISTS idx_positions_one_open_per_security_horizon;
DROP TABLE IF EXISTS positions;

DROP TABLE IF EXISTS books;

DROP INDEX IF EXISTS idx_suppressed_security;
DROP INDEX IF EXISTS idx_suppressed_run;
DROP TABLE IF EXISTS suppressed_signals;

DROP TRIGGER IF EXISTS research_candidates_no_delete;
DROP TRIGGER IF EXISTS research_candidates_no_update;
DROP INDEX IF EXISTS idx_candidates_run;
DROP INDEX IF EXISTS idx_candidates_cutoff;
DROP INDEX IF EXISTS idx_candidates_security;
DROP TABLE IF EXISTS research_candidates;
