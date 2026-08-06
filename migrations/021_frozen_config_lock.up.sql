-- 021 up: frozen_config_lock -- ties an official strategy_version to the
-- exact config.frozen.json bytes and the calibration evidence that justified
-- it.
--
-- WHY THIS EXISTS. composite_threshold moved from the declared null
-- placeholder to a real, frozen value (55) in Phase S4. The existing
-- _governed_by / _version_digests mechanism (config_loader.py) already
-- blocks LOADING the config at all if any governed value drifts without a
-- version bump -- composite_threshold now joins that list under
-- selection_rule_version. This table is a narrower, additional guarantee on
-- top of it: even a config that loads cleanly (every version bumped
-- correctly) must match the exact byte-for-byte hash recorded here before
-- selection/compute.py will generate an OFFICIAL candidate (fixture
-- population, real composite_threshold, no --provisional-threshold
-- override). A config edit that bumps every version correctly but still
-- drifts from what was actually calibrated is caught here, not just at load
-- time.
--
-- Append-only, like xbrl_facts and research_candidates: a lock that could be
-- silently rewritten would defeat the point of it existing. Re-locking means
-- bumping strategy_version and inserting a new row, never editing this one.

CREATE TABLE IF NOT EXISTS frozen_config_lock (
    strategy_version       INTEGER PRIMARY KEY,
    selection_rule_version INTEGER NOT NULL,
    config_hash            TEXT NOT NULL,
    calibration_report_id  TEXT NOT NULL REFERENCES calibration_reports (report_id),
    locked_at              TEXT NOT NULL   -- UTC
);

CREATE TRIGGER IF NOT EXISTS frozen_config_lock_no_update
BEFORE UPDATE ON frozen_config_lock
BEGIN
    SELECT RAISE(ABORT, 'frozen_config_lock is append-only: UPDATE is forbidden. Bump strategy_version and insert a new lock row.');
END;

CREATE TRIGGER IF NOT EXISTS frozen_config_lock_no_delete
BEFORE DELETE ON frozen_config_lock
BEGIN
    SELECT RAISE(ABORT, 'frozen_config_lock is append-only: DELETE is forbidden.');
END;
