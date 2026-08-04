-- 017 up: S2 scaled-ingestion resumability.
--
-- The four ingestion stages (prices, Form 4, SEC facts, universe membership)
-- each wrap their whole per-invocation batch in one transaction. That is
-- correct for atomicity but means a killed process loses everything since
-- the last commit -- resuming would have to redo the entire batch, which
-- defeats the point of running at real (500-4000 security) scale.
--
-- orchestration_progress is per-item bookkeeping, keyed by a caller-supplied
-- batch_id so re-invoking with the SAME batch_id after a kill resumes: items
-- already 'success' are skipped, 'failed' or never-attempted ones are
-- retried. It is operational state, not evidence -- unlike research_candidates
-- or filing_verifications, a retry legitimately overwrites the row for the
-- same (batch_id, stage, item_key), the same way source_health already
-- tracks "current" state rather than a permanent log.
--
-- This table does not replace pipeline_runs. Each orchestrated invocation
-- still writes its own pipeline_runs row (stage='orchestrate_<tier>'), so
-- /health's existing run-history reporting needs no changes. A killed run
-- leaves its pipeline_runs row at status='running' forever, exactly like
-- every other stage already behaves when killed -- this table is what makes
-- the NEXT invocation for the same batch_id skip the work already done,
-- not a replacement for that existing convention.

CREATE TABLE IF NOT EXISTS orchestration_progress (
    batch_id     TEXT NOT NULL,
    stage        TEXT NOT NULL CHECK (stage IN ('prices', 'form4', 'xbrl', 'universe')),
    -- Symbol for prices/form4/xbrl (one row per security or per CIK); the
    -- literal string 'snapshot' for universe, which is one holistic
    -- computation rather than a per-security operation.
    item_key     TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    attempted_at TEXT NOT NULL,   -- UTC
    error        TEXT,
    run_id       TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    PRIMARY KEY (batch_id, stage, item_key)
);

CREATE INDEX IF NOT EXISTS idx_orchestration_progress_status
    ON orchestration_progress (batch_id, stage, status);
