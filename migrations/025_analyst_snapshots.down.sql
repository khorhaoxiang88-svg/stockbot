-- 025 down: drop the analyst consensus snapshot table. securities is
-- untouched -- this migration never owned it.

DROP TRIGGER IF EXISTS analyst_snapshots_no_delete;
DROP TRIGGER IF EXISTS analyst_snapshots_no_update;
DROP INDEX IF EXISTS idx_analyst_snapshots_security;
DROP TABLE IF EXISTS analyst_snapshots;
