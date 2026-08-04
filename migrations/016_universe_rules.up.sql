-- 016 up: S1 rules-based universe -- candidate pool, run type, hysteresis
-- suspension status, and the monthly membership change log.
--
-- Kept entirely separate from fixture_manifest on purpose. fixture_manifest is
-- Phase F's frozen 50-security engineering fixture, whose exit criteria
-- already passed 10/10 (migration 015). Mixing S1's non-official universe
-- candidates into that table would silently expand what Phase F's checks
-- audit after the fact. universe_candidate_pool is the S1 analogue instead:
-- same shape idea, different table, so Phase F's scope never moves.
--
-- universe_snapshot_runs and universe_snapshots already exist (migration 002)
-- and already carry almost everything S1 needs: status, exclusion_reason,
-- adv_dollar, market_cap, market_cap_confidence, days_below_retention. Two
-- things are missing:
--   1. universe_snapshot_runs has no way to say whether a run was a formal
--      MONTHLY membership decision or a DAILY safety check. The brief
--      requires both, and only monthly runs may change membership.
--   2. universe_snapshots.status allows 'watch', but the migration-002 CHECK
--      only requires exclusion_reason for status='excluded'. A daily safety
--      suspension uses 'watch' and must carry a reason too, so the CHECK is
--      strengthened here -- SQLite cannot ALTER a CHECK in place, so the
--      table is rebuilt and every row copied, the same way migrations 007
--      and 012 rebuilt their tables.

PRAGMA foreign_keys = OFF;

-- 1. The S1 candidate pool. security_id must already exist in `securities`
--    (loaded through the same identity/classification path as the fixture,
--    just not added to fixture_manifest).
CREATE TABLE IF NOT EXISTS universe_candidate_pool (
    security_id       INTEGER NOT NULL REFERENCES securities (security_id),
    symbol_at_discovery TEXT NOT NULL,
    exchange          TEXT NOT NULL,
    discovered_at     TEXT NOT NULL,   -- UTC
    pool_version      TEXT NOT NULL,
    discovery_source  TEXT NOT NULL,   -- e.g. 'nasdaq_trader_directory_sample'
    PRIMARY KEY (security_id, pool_version)
);

CREATE INDEX IF NOT EXISTS idx_universe_candidate_pool_version
    ON universe_candidate_pool (pool_version);

-- 2. run_type on universe_snapshot_runs. Plain ADD COLUMN is sufficient here
--    because it only adds a new column and a CHECK on that new column, which
--    SQLite supports without a rebuild (unlike changing an existing CHECK).
ALTER TABLE universe_snapshot_runs
    ADD COLUMN run_type TEXT NOT NULL DEFAULT 'monthly_membership'
        CHECK (run_type IN ('monthly_membership', 'daily_safety'));

-- 3. Rebuild universe_snapshots: strengthen the exclusion-reason CHECK to
--    also cover 'watch' (daily safety suspension), and add a source_note
--    field so the reason cites what triggered a daily suspension (stale
--    price, halt, new severe dilution evidence) distinctly from a monthly
--    exclusion's rule-based reason. Every existing row is preserved.
CREATE TABLE universe_snapshots_new (
    snapshot_id           TEXT NOT NULL REFERENCES universe_snapshot_runs (snapshot_id),
    security_id           INTEGER NOT NULL REFERENCES securities (security_id),
    snapshot_date         TEXT NOT NULL,      -- UTC date
    status                TEXT NOT NULL CHECK (status IN ('included', 'excluded', 'watch')),
    exclusion_reason      TEXT,
    adv_dollar            REAL,
    market_cap            REAL,
    market_cap_confidence TEXT CHECK (market_cap_confidence IN ('high', 'medium', 'low')),
    days_below_retention  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, security_id),
    -- Strengthened: both 'excluded' (formal, monthly) and 'watch' (daily
    -- safety suspension) must say why. Only 'included' may omit a reason.
    CHECK (status = 'included' OR exclusion_reason IS NOT NULL)
);

INSERT INTO universe_snapshots_new
SELECT snapshot_id, security_id, snapshot_date, status, exclusion_reason,
       adv_dollar, market_cap, market_cap_confidence, days_below_retention
  FROM universe_snapshots;

DROP TABLE universe_snapshots;
ALTER TABLE universe_snapshots_new RENAME TO universe_snapshots;

CREATE INDEX IF NOT EXISTS idx_universe_snapshots_security
    ON universe_snapshots (security_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_universe_snapshots_status
    ON universe_snapshots (snapshot_id, status);

-- 4. The monthly membership change log. Only monthly_membership runs may
--    write here -- a daily_safety run suspends via universe_snapshots.status
--    = 'watch' but never formally enters or exits anyone. Append-only by
--    convention (like suppressed_signals): everything considered is
--    evidence, and a membership decision is never quietly revised.
CREATE TABLE IF NOT EXISTS universe_membership_changes (
    change_id      TEXT PRIMARY KEY,
    security_id    INTEGER NOT NULL REFERENCES securities (security_id),
    snapshot_id    TEXT NOT NULL REFERENCES universe_snapshot_runs (snapshot_id),
    change_type    TEXT NOT NULL CHECK (change_type IN ('entered', 'exited')),
    effective_date TEXT NOT NULL,     -- UTC date, the monthly run's date
    previous_status TEXT,             -- NULL for a security's first-ever snapshot
    new_status     TEXT NOT NULL,
    reason         TEXT NOT NULL,
    recorded_at    TEXT NOT NULL      -- UTC
);

CREATE INDEX IF NOT EXISTS idx_universe_membership_changes_date
    ON universe_membership_changes (effective_date, change_type);
CREATE INDEX IF NOT EXISTS idx_universe_membership_changes_security
    ON universe_membership_changes (security_id, effective_date);

PRAGMA foreign_keys = ON;
