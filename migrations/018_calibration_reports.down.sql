-- 018 down: drop calibration report storage. Wholly re-derivable by re-running
-- pipeline/calibration/report.py; nothing here is irreplaceable evidence.

DROP INDEX IF EXISTS idx_calibration_reports_computed;
DROP TABLE IF EXISTS calibration_reports;
