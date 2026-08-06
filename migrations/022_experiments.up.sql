-- 022 up: the official forward experiment, and the query-layer views that
-- permanently exclude fixture/Phase S paper trades from official statistics.
--
-- NUMBERING: the brief calls this "migration 013", but 013 (paper_positions)
-- was already applied months ago. Renumbered rather than editing an applied
-- migration, the same resolution two earlier briefs got (see README).
--
-- WHY A SEPARATE experiments TABLE, not another column on universe_snapshot_runs.
-- is_official already exists there, but means two different things depending
-- on caller: F8's scoring snapshot sets it for the fixture's percentile
-- comparison population (ensure_snapshot, scoring/compute.py), and S1/S2's
-- membership snapshot (universe/membership.py) has always hardcoded it to 0.
-- Neither of those is "the forward-trading universe as of launch". experiments
-- is a new, unambiguous record of that one specific event: the exact
-- strategy_version, selection_rule_version, protocol_version and config_hash
-- the experiment launched under, and when. At most one row may be 'active' at
-- a time (partial unique index below) -- there is one official timeline.
--
-- IMMUTABLE, BUT NOT FULLY: experiment_id, strategy_version,
-- selection_rule_version, protocol_version, config_hash and started_at can
-- never change once set -- they define what was launched and when, and
-- rewriting them would rewrite history. status, ended_at and
-- compromised_reason are the one exception: an active experiment has no end
-- date yet by definition, and must be able to transition to 'ended' or
-- 'compromised' exactly once. The trigger below blocks changes to the
-- immutable columns specifically, not every UPDATE.

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id           TEXT PRIMARY KEY,
    strategy_version        INTEGER NOT NULL,
    selection_rule_version  INTEGER NOT NULL,
    protocol_version        INTEGER NOT NULL,
    config_hash             TEXT NOT NULL,
    started_at              TEXT NOT NULL,   -- UTC
    ended_at                TEXT,            -- UTC
    status                  TEXT NOT NULL CHECK (status IN ('active', 'ended', 'compromised')),
    compromised_reason      TEXT,

    CHECK (status <> 'active' OR (ended_at IS NULL AND compromised_reason IS NULL)),
    CHECK (status <> 'ended' OR ended_at IS NOT NULL),
    CHECK (status <> 'compromised' OR compromised_reason IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_experiments_one_active
    ON experiments (status) WHERE status = 'active';

CREATE TRIGGER IF NOT EXISTS experiments_core_immutable
BEFORE UPDATE ON experiments
WHEN NEW.experiment_id != OLD.experiment_id
  OR NEW.strategy_version != OLD.strategy_version
  OR NEW.selection_rule_version != OLD.selection_rule_version
  OR NEW.protocol_version != OLD.protocol_version
  OR NEW.config_hash != OLD.config_hash
  OR NEW.started_at != OLD.started_at
BEGIN
    SELECT RAISE(ABORT, 'experiments: experiment_id, strategy_version, selection_rule_version, protocol_version, config_hash and started_at are immutable once set. Only status, ended_at and compromised_reason may change, and only once.');
END;

CREATE TRIGGER IF NOT EXISTS experiments_no_delete
BEFORE DELETE ON experiments
BEGIN
    SELECT RAISE(ABORT, 'experiments rows are never deleted.');
END;

-- ------------------------------------------------------- query-layer enforcement
--
-- "Enforced in the query layer, not by convention": every reader of official
-- statistics (web and pipeline alike) reads through these views, never
-- research_candidates/paper_positions/benchmark_positions directly for an
-- official number. A row qualifies only if BOTH hold:
--   1. it came from a genuine official selection attempt -- code_version
--      carries no '+provisional' or '+pool[...]' suffix (selection/compute.py
--      only ever appends one when the run was NOT a real official attempt);
--   2. it was generated at or after the active experiment's started_at --
--      the permanent, structural cutoff. Nothing before it is ever merged,
--      backfilled or retroactively counted, and nothing can be, because nothing
--      generated before started_at can ever satisfy this condition -- nothing
--      you can do to research_candidates rows changes when they were generated.
--
-- Matching '%+%' works because no legitimate code_version contains a literal
-- '+' -- only the provisional/pool suffixes ever add one.

CREATE VIEW IF NOT EXISTS official_candidates AS
SELECT rc.*
  FROM research_candidates rc
  JOIN experiments e ON e.status = 'active'
 WHERE rc.code_version NOT LIKE '%+%'
   AND rc.generated_at >= e.started_at;

CREATE VIEW IF NOT EXISTS official_positions AS
SELECT pp.*
  FROM paper_positions pp
  JOIN research_candidates rc ON rc.candidate_id = pp.candidate_id
  JOIN experiments e ON e.status = 'active'
 WHERE rc.code_version NOT LIKE '%+%'
   AND rc.generated_at >= e.started_at;

CREATE VIEW IF NOT EXISTS official_benchmark_positions AS
SELECT bp.*
  FROM benchmark_positions bp
  JOIN research_candidates rc ON rc.candidate_id = bp.candidate_id
  JOIN experiments e ON e.status = 'active'
 WHERE rc.code_version NOT LIKE '%+%'
   AND rc.generated_at >= e.started_at;
