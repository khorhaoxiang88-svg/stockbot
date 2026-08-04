-- 016 down: undo the S1 universe-rules additions.
--
-- universe_membership_changes and universe_candidate_pool are wholly derived
-- / discovery-only (no human verification work like filing_verifications), so
-- dropping them is safe. universe_snapshots is rebuilt back to its
-- migration-002 shape, and run_type is dropped from universe_snapshot_runs.

PRAGMA foreign_keys = OFF;

DROP INDEX IF EXISTS idx_universe_membership_changes_security;
DROP INDEX IF EXISTS idx_universe_membership_changes_date;
DROP TABLE IF EXISTS universe_membership_changes;

DROP INDEX IF EXISTS idx_universe_snapshots_status;
DROP INDEX IF EXISTS idx_universe_snapshots_security;

CREATE TABLE universe_snapshots_old (
    snapshot_id           TEXT NOT NULL REFERENCES universe_snapshot_runs (snapshot_id),
    security_id           INTEGER NOT NULL REFERENCES securities (security_id),
    snapshot_date         TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('included', 'excluded', 'watch')),
    exclusion_reason      TEXT,
    adv_dollar            REAL,
    market_cap            REAL,
    market_cap_confidence TEXT CHECK (market_cap_confidence IN ('high', 'medium', 'low')),
    days_below_retention  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, security_id),
    CHECK (status <> 'excluded' OR exclusion_reason IS NOT NULL)
);

INSERT INTO universe_snapshots_old
SELECT snapshot_id, security_id, snapshot_date, status, exclusion_reason,
       adv_dollar, market_cap, market_cap_confidence, days_below_retention
  FROM universe_snapshots;

DROP TABLE universe_snapshots;
ALTER TABLE universe_snapshots_old RENAME TO universe_snapshots;

CREATE INDEX IF NOT EXISTS idx_universe_snapshots_security
    ON universe_snapshots (security_id, snapshot_date);

ALTER TABLE universe_snapshot_runs DROP COLUMN run_type;

DROP INDEX IF EXISTS idx_universe_candidate_pool_version;
DROP TABLE IF EXISTS universe_candidate_pool;

PRAGMA foreign_keys = ON;
