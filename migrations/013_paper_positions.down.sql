-- 013 down: drop the execution tables and restore the F10 `positions` table.
--
-- Restoring `positions` matters: pipeline/selection reads it at the rolled-back
-- revision, and leaving it absent would break candidate selection rather than
-- merely undoing F11. It comes back exactly as migration 011 created it.
--
-- Paper positions are NOT recoverable by recomputation. A position records what
-- the protocol did with the prices available on each day it was evaluated, and
-- a later re-run would resolve a pending delisting using evidence that arrived
-- afterwards. Rolling back loses that permanently; this file exists for undoing
-- a bad deployment, not for routine use.

PRAGMA foreign_keys = OFF;

DROP INDEX IF EXISTS idx_position_events_position;
DROP TABLE IF EXISTS position_events;

DROP INDEX IF EXISTS idx_cancelled_entries_run;
DROP TABLE IF EXISTS cancelled_entries;

DROP INDEX IF EXISTS idx_benchmark_positions_candidate;
DROP TABLE IF EXISTS benchmark_positions;

DROP INDEX IF EXISTS idx_paper_positions_exit;
DROP INDEX IF EXISTS idx_paper_positions_candidate;
DROP INDEX IF EXISTS idx_paper_positions_book;
DROP TABLE IF EXISTS paper_positions;

CREATE TABLE IF NOT EXISTS positions (
    position_id   TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES books (book_id),
    candidate_id  TEXT NOT NULL REFERENCES research_candidates (candidate_id),
    security_id   INTEGER NOT NULL REFERENCES securities (security_id),
    horizon_days  INTEGER NOT NULL,

    status        TEXT NOT NULL CHECK (status IN
                       ('pending', 'open', 'closed', 'gap_cancelled')),
    notional      REAL NOT NULL,

    opened_on     TEXT,
    closed_on     TEXT,
    exit_reason   TEXT,

    CHECK (status <> 'open'          OR (opened_on IS NOT NULL AND closed_on IS NULL)),
    CHECK (status <> 'closed'        OR (opened_on IS NOT NULL AND closed_on IS NOT NULL
                                         AND exit_reason IS NOT NULL)),
    CHECK (status <> 'gap_cancelled' OR (opened_on IS NULL AND closed_on IS NOT NULL)),
    CHECK (notional > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_one_open_per_security_horizon
    ON positions (security_id, horizon_days) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_positions_book ON positions (book_id, status);
CREATE INDEX IF NOT EXISTS idx_positions_cooldown
    ON positions (security_id, closed_on DESC, status);

PRAGMA foreign_keys = ON;
