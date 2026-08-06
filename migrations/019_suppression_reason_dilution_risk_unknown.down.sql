-- 019 down: remove the 'dilution_or_riskflags_unknown' suppression reason.
--
-- Rows carrying the code cannot survive a rollback, because the constraint
-- they would land under does not permit them. They are DELETED rather than
-- silently rewritten to another reason: a suppression that says something
-- different from what was decided is worse than no row. Re-running selection
-- after a roll-forward restores them, since suppressions are wholly derived.

PRAGMA foreign_keys = OFF;

CREATE TABLE suppressed_signals_old (
    run_id             TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    security_id        INTEGER NOT NULL REFERENCES securities (security_id),
    horizon_days       INTEGER NOT NULL,
    composite          REAL,
    "rank"             INTEGER,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                            -- eligibility
                            'not_rankable',
                            'model_not_applicable',
                            'dilution_disqualified',
                            'risk_flag_going_concern',
                            'risk_flag_dilution_disqualify',
                            'below_composite_threshold',
                            'composite_threshold_unset',
                            'stale_source',
                            -- cooldowns
                            'cooldown_recent_exit',
                            'cooldown_gap_cancelled',
                            -- capacity and caps
                            'open_position',
                            'book_capacity',
                            'cohort_cap',
                            'selection_cap')),
    detail             TEXT,

    PRIMARY KEY (run_id, security_id, horizon_days, suppression_reason)
);

INSERT INTO suppressed_signals_old
    (run_id, security_id, horizon_days, composite, "rank", suppression_reason, detail)
SELECT run_id, security_id, horizon_days, composite, "rank", suppression_reason, detail
  FROM suppressed_signals
 WHERE suppression_reason <> 'dilution_or_riskflags_unknown';

DROP TABLE suppressed_signals;
ALTER TABLE suppressed_signals_old RENAME TO suppressed_signals;

CREATE INDEX IF NOT EXISTS idx_suppressed_run
    ON suppressed_signals (run_id, suppression_reason);
CREATE INDEX IF NOT EXISTS idx_suppressed_security
    ON suppressed_signals (security_id, run_id);

PRAGMA foreign_keys = ON;
