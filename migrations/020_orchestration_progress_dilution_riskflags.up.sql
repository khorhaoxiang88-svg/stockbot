-- 020 up: add 'dilution' and 'riskflags' orchestration_progress stages.
--
-- Auditing Phase S found dilution/compute.py and riskflags/compute.py had
-- never run against the S1/S2 pool at all -- both were fixture-only, unlike
-- prices/Form4/XBRL/universe which S2 already scaled. orchestrate/run.py now
-- has run_dilution_tier and run_riskflags_tier, the same per-item-transaction,
-- resumable shape as the other four tiers, but `stage` is a CHECK constraint,
-- which SQLite cannot alter in place. Same rebuild-and-copy pattern as
-- migration 012 (risk_flags) and 019 (suppressed_signals).
--
-- Nothing else about the table changes. Every existing row is preserved.

CREATE TABLE orchestration_progress_new (
    batch_id     TEXT NOT NULL,
    stage        TEXT NOT NULL CHECK (stage IN (
                     'prices', 'form4', 'xbrl', 'universe', 'dilution', 'riskflags')),
    item_key     TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    attempted_at TEXT NOT NULL,
    error        TEXT,
    run_id       TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    PRIMARY KEY (batch_id, stage, item_key)
);

INSERT INTO orchestration_progress_new
    (batch_id, stage, item_key, status, attempted_at, error, run_id)
SELECT batch_id, stage, item_key, status, attempted_at, error, run_id
  FROM orchestration_progress;

DROP TABLE orchestration_progress;
ALTER TABLE orchestration_progress_new RENAME TO orchestration_progress;

CREATE INDEX IF NOT EXISTS idx_orchestration_progress_status
    ON orchestration_progress (batch_id, stage, status);
