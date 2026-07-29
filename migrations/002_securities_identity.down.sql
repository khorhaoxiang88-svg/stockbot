-- 002 down: drop everything 002 created, children before parents.

DROP INDEX IF EXISTS idx_fixture_manifest_version;
DROP TABLE IF EXISTS fixture_manifest;

DROP INDEX IF EXISTS idx_universe_snapshots_security;
DROP TABLE IF EXISTS universe_snapshots;
DROP TABLE IF EXISTS universe_snapshot_runs;

DROP INDEX IF EXISTS idx_listings_symbol_window;
DROP TABLE IF EXISTS listings;

DROP INDEX IF EXISTS idx_securities_type;
DROP INDEX IF EXISTS idx_securities_cik;
DROP TABLE IF EXISTS securities;

-- Note on the never-reuse guarantee: DROP TABLE also removes the securities
-- row from sqlite_sequence, so a rolled-back-and-reapplied database starts
-- issuing security_id from 1 again. Inside a live database the guarantee
-- holds, because securities rows are never deleted (delisting sets
-- is_active = 0 instead). Rolling back 002 destroys the identity table by
-- definition, so treat it as a development-only action, never as a
-- production repair step.
