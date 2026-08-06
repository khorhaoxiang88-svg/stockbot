-- 019 up: add the 'dilution_or_riskflags_unknown' suppression reason.
--
-- WHY A MIGRATION FOR ONE ENUM VALUE. Auditing Phase S found that
-- selection/compute.py's load_rows() (and calibration/report.py's own copy)
-- defaulted a security with no dilution_signals row, or no risk_flags row at
-- all, to dilution_score=0.0 and high_going_concern=False -- read as "checked,
-- clean", which was false: dilution/compute.py and riskflags/compute.py had
-- never run against the S1/S2 pool. That is the zero-fill rule 5 forbids
-- everywhere else in this system, and for the pool it meant every unscreened
-- security was silently treated as risk-free.
--
-- The fix excludes such a security from selection rather than assuming it
-- clean, and logs it suppressed -- "everything considered and not selected is
-- logged with a reason" -- so it needs its own reason code, distinct from
-- every existing eligibility reason, none of which mean "we don't know".
-- suppression_reason is a CHECK constraint, which SQLite cannot alter in
-- place, so the table is rebuilt and the rows copied, the same way migration
-- 012 added 'overdue_issuer_filing' to risk_flags.
--
-- Nothing else about the table changes. Every existing row is preserved.

PRAGMA foreign_keys = OFF;

CREATE TABLE suppressed_signals_new (
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
                            'dilution_or_riskflags_unknown',
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

INSERT INTO suppressed_signals_new
    (run_id, security_id, horizon_days, composite, "rank", suppression_reason, detail)
SELECT run_id, security_id, horizon_days, composite, "rank", suppression_reason, detail
  FROM suppressed_signals;

DROP TABLE suppressed_signals;
ALTER TABLE suppressed_signals_new RENAME TO suppressed_signals;

CREATE INDEX IF NOT EXISTS idx_suppressed_run
    ON suppressed_signals (run_id, suppression_reason);
CREATE INDEX IF NOT EXISTS idx_suppressed_security
    ON suppressed_signals (security_id, run_id);

PRAGMA foreign_keys = ON;
