-- 001 down: remove the operations tables created by 001.
-- schema_migrations is intentionally NOT dropped: it is the ledger the runner
-- uses to know what is applied, and the runner recreates it on every connect.

DROP INDEX IF EXISTS idx_pipeline_runs_stage_started;
DROP TABLE IF EXISTS pipeline_runs;
DROP TABLE IF EXISTS source_health;
