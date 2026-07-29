-- 001 up: operations tables only. No market data tables in this migration.
-- All timestamp columns store UTC ISO-8601 text, e.g. 2026-07-29T13:45:00Z.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id          TEXT PRIMARY KEY,
    stage           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'partial')),
    records_written INTEGER NOT NULL DEFAULT 0,
    code_version    TEXT,
    errors_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_stage_started
    ON pipeline_runs (stage, started_at DESC);

CREATE TABLE IF NOT EXISTS source_health (
    source_name          TEXT PRIMARY KEY,
    last_success         TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    staleness_hours      REAL,
    coverage_pct         REAL
);
