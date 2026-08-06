-- 022 down: drop the official views, experiments table and its guards.

DROP VIEW IF EXISTS official_benchmark_positions;
DROP VIEW IF EXISTS official_positions;
DROP VIEW IF EXISTS official_candidates;

DROP TRIGGER IF EXISTS experiments_no_delete;
DROP TRIGGER IF EXISTS experiments_core_immutable;
DROP INDEX IF EXISTS idx_experiments_one_active;
DROP TABLE IF EXISTS experiments;
