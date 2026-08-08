-- 023 up: O3's disclosure/change-control phase. Two things, both about the
-- same guarantee -- an official result is never silently rewritten:
--
--   1. defect_log: the audit trail the bug-correction policy publishes to
--      (/changelog). A defect record is itself append-only in the same
--      shape as this project's other permanent records (frozen_config_lock,
--      experiments): core facts (what was found, when, how severe, what it
--      affected) are immutable once set; resolution, new_strategy_version
--      and published_at fill in later as the investigation concludes, each
--      exactly once.
--
--   2. Closing paper_positions/benchmark_positions once (status='closed')
--      already only ever happens once in the pipeline -- every closing
--      UPDATE in execution/compute.py is immediately followed by `return`,
--      and every other UPDATE on these tables only ever runs while a
--      position is still open or pending_resolution. research_candidates
--      already has this exact protection (migration 011); these two never
--      had it, which was a real gap, not a deliberate omission -- an
--      already-closed result being editable was never how the pipeline
--      used it, just something schema-level enforcement had not yet
--      caught up to. No existing code path is affected: the triggers below
--      only ever fire on an attempt to rewrite a row already at rest.

CREATE TABLE IF NOT EXISTS defect_log (
    defect_id                 TEXT PRIMARY KEY,
    discovered_at              TEXT NOT NULL,   -- UTC
    severity                   TEXT NOT NULL CHECK (severity IN (
                                   'cosmetic', 'data_correction', 'material')),
    description                TEXT NOT NULL,
    -- Which locked strategy the defect affected, if any. Required for a
    -- material defect ("material ... defect affecting an official
    -- candidate" always names one); optional for cosmetic/data_correction,
    -- which may implicate no strategy_version at all (e.g. a chart axis
    -- label) or one without compromising it.
    affected_strategy_version  INTEGER REFERENCES frozen_config_lock (strategy_version),
    affected_candidates_json   TEXT,            -- JSON array of candidate_id, nullable
    resolution                 TEXT,            -- filled in once known
    -- Set only when a material defect actually spawned a new, separately-
    -- reported strategy_version. Never set for cosmetic/data_correction --
    -- those never restart the experiment by policy.
    new_strategy_version        INTEGER REFERENCES frozen_config_lock (strategy_version),
    published_at                TEXT,            -- UTC; NULL until published on /changelog

    CHECK (severity <> 'material' OR affected_strategy_version IS NOT NULL),
    CHECK (new_strategy_version IS NULL OR severity = 'material'),
    CHECK (new_strategy_version IS NULL OR new_strategy_version <> affected_strategy_version)
);

CREATE INDEX IF NOT EXISTS idx_defect_log_published
    ON defect_log (published_at);

CREATE TRIGGER IF NOT EXISTS defect_log_core_immutable
BEFORE UPDATE ON defect_log
WHEN NEW.defect_id != OLD.defect_id
  OR NEW.discovered_at != OLD.discovered_at
  OR NEW.severity != OLD.severity
  OR NEW.description != OLD.description
  OR NEW.affected_strategy_version IS NOT OLD.affected_strategy_version
  OR NEW.affected_candidates_json IS NOT OLD.affected_candidates_json
BEGIN
    SELECT RAISE(ABORT, 'defect_log: defect_id, discovered_at, severity, description, affected_strategy_version and affected_candidates_json are immutable once set. Only resolution, new_strategy_version and published_at may be filled in.');
END;

CREATE TRIGGER IF NOT EXISTS defect_log_no_delete
BEFORE DELETE ON defect_log
BEGIN
    SELECT RAISE(ABORT, 'defect_log rows are never deleted.');
END;

-- ------------------------------------------------- result immutability

CREATE TRIGGER IF NOT EXISTS paper_positions_closed_immutable
BEFORE UPDATE ON paper_positions
WHEN OLD.status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'paper_positions: a closed position is immutable. A correction goes through the bug-correction policy (defect_log), never an UPDATE.');
END;

CREATE TRIGGER IF NOT EXISTS paper_positions_no_delete
BEFORE DELETE ON paper_positions
BEGIN
    SELECT RAISE(ABORT, 'paper_positions rows are never deleted.');
END;

CREATE TRIGGER IF NOT EXISTS benchmark_positions_closed_immutable
BEFORE UPDATE ON benchmark_positions
WHEN OLD.status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'benchmark_positions: a closed position is immutable. A correction goes through the bug-correction policy (defect_log), never an UPDATE.');
END;

CREATE TRIGGER IF NOT EXISTS benchmark_positions_no_delete
BEFORE DELETE ON benchmark_positions
BEGIN
    SELECT RAISE(ABORT, 'benchmark_positions rows are never deleted.');
END;
