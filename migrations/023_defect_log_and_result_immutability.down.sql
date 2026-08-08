-- 023 down: drop the result-immutability guards and defect_log.

DROP TRIGGER IF EXISTS benchmark_positions_no_delete;
DROP TRIGGER IF EXISTS benchmark_positions_closed_immutable;
DROP TRIGGER IF EXISTS paper_positions_no_delete;
DROP TRIGGER IF EXISTS paper_positions_closed_immutable;

DROP TRIGGER IF EXISTS defect_log_no_delete;
DROP TRIGGER IF EXISTS defect_log_core_immutable;
DROP INDEX IF EXISTS idx_defect_log_published;
DROP TABLE IF EXISTS defect_log;
