-- 018 up: S3 calibration report storage.
--
-- The report is computed once in Python (pipeline/calibration/report.py,
-- which owns the selection-rule simulation via selection.rules.select --
-- unchanged, not reimplemented) and stored whole. The web page reads and
-- renders this JSON verbatim, the same pattern F8's explanation_json already
-- uses: the page cannot disagree with what was actually computed, because it
-- is not computing anything itself.

CREATE TABLE IF NOT EXISTS calibration_reports (
    report_id    TEXT PRIMARY KEY,
    computed_at  TEXT NOT NULL,   -- UTC
    score_date   TEXT NOT NULL,
    config_hash  TEXT NOT NULL,
    report_json  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calibration_reports_computed
    ON calibration_reports (computed_at DESC);
