-- 009 down: drop the scores table and its view.
--
-- Scores are wholly derived: every one of them can be recomputed from prices,
-- fundamentals, insider transactions and dilution signals, all of which survive
-- this rollback. So dropping is safe here in a way it is not for xbrl_facts.

DROP VIEW IF EXISTS latest_scores;
DROP INDEX IF EXISTS idx_scores_cohort;
DROP INDEX IF EXISTS idx_scores_security;
DROP INDEX IF EXISTS idx_scores_ranking;
DROP TABLE IF EXISTS scores;
