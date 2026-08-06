-- 020 down: remove the 'dilution' and 'riskflags' orchestration_progress stages.
--
-- Rows carrying either stage cannot survive a rollback, because the
-- constraint they would land under does not permit them. Deleted rather than
-- rewritten: orchestration progress is operational bookkeeping (017's own
-- words), and a wrong stage value would make a future resume skip work it
-- never actually did. Re-running the tier after a roll-forward regenerates
-- these rows from scratch.

CREATE TABLE orchestration_progress_old (
    batch_id     TEXT NOT NULL,
    stage        TEXT NOT NULL CHECK (stage IN ('prices', 'form4', 'xbrl', 'universe')),
    item_key     TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    attempted_at TEXT NOT NULL,
    error        TEXT,
    run_id       TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    PRIMARY KEY (batch_id, stage, item_key)
);

INSERT INTO orchestration_progress_old
    (batch_id, stage, item_key, status, attempted_at, error, run_id)
SELECT batch_id, stage, item_key, status, attempted_at, error, run_id
  FROM orchestration_progress
 WHERE stage NOT IN ('dilution', 'riskflags');

DROP TABLE orchestration_progress;
ALTER TABLE orchestration_progress_old RENAME TO orchestration_progress;

CREATE INDEX IF NOT EXISTS idx_orchestration_progress_status
    ON orchestration_progress (batch_id, stage, status);
