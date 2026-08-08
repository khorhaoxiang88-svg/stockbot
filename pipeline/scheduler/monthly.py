"""S6 monthly job: universe membership recalculation with hysteresis.

Runs pipeline/orchestrate/run.py --tier universe --run-type monthly_membership
against the current pool -- the same call the S1/S2 sessions already made by
hand. The hysteresis itself (days_below_retention, exclusion thresholds)
lives in universe/membership.py's compute_snapshot and is untouched here;
this script only schedules that existing call and logs the result.

Missed-run detection uses a 30-day period with a 5-day grace: real month
lengths (28-31 days) never trip it on their own, only an actually-skipped
month does (~60 day gap).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from scheduler.common import (  # noqa: E402
    existing_log_dates,
    latest_pool_version,
    missed_run_dates,
    record_scheduler_run,
    run_stage,
    utc_today,
    RunLog,
)

JOB = "monthly"
MISSED_RUN_PERIOD_DAYS = 30
MISSED_RUN_GRACE_DAYS = 5


def _log_missed_runs(conn, log: RunLog, today: date) -> list[str]:
    dates = existing_log_dates(JOB)
    last = dates[-1] if dates else None
    missed = missed_run_dates(last, today, MISSED_RUN_PERIOD_DAYS, MISSED_RUN_GRACE_DAYS)
    if missed:
        log.section("MISSED RUNS DETECTED")
        for d in missed:
            log.line(f"  no {JOB} log found near {d.isoformat()}")
        errors = [f"missed {JOB} run near: {d.isoformat()}" for d in missed]
        record_scheduler_run(conn, f"{JOB}_missed", "failed", 0, errors)
        return errors
    return []


def run_monthly(db: str, today: str | None = None) -> int:
    today = today or utc_today()
    log = RunLog(JOB, today)
    conn = migrate.connect(Path(db))

    missed_errors = _log_missed_runs(conn, log, date.fromisoformat(today))

    pool = latest_pool_version(conn)
    log.section("pool")
    log.line(f"  {pool or '(none loaded -- falling back to fixture securities)'}")

    log.section("stage: universe membership")
    args = ["pipeline/orchestrate/run.py", "--tier", "universe",
            "--run-type", "monthly_membership", "--db", db]
    if pool:
        args += ["--pool", pool]
    result = run_stage("universe", args)
    log.line(f"  ok={result.ok} returncode={result.returncode}")
    if result.stdout_tail:
        log.line("  --- stdout (tail) ---")
        log.line(result.stdout_tail)
    if not result.ok and result.stderr_tail:
        log.line("  --- stderr (tail) ---")
        log.line(result.stderr_tail)

    snapshot = None
    if result.ok:
        snapshot = conn.execute(
            "SELECT snapshot_id, security_count FROM universe_snapshot_runs "
            "WHERE run_type = 'monthly_membership' ORDER BY effective_at DESC LIMIT 1"
        ).fetchone()
        if snapshot:
            log.line(f"  snapshot_id={snapshot['snapshot_id']} "
                      f"security_count={snapshot['security_count']}")

    log_path = log.write()

    status = "success" if result.ok and not missed_errors else (
        "failed" if not result.ok else "partial"
    )
    errors = missed_errors + ([] if result.ok else ["universe stage failed"])
    record_scheduler_run(
        conn, JOB, status, snapshot["security_count"] if snapshot else 0, errors
    )

    conn.close()
    print(f"monthly run {today}: status={status} log={log_path}")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S6 monthly scheduled run")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=None, help="defaults to today (UTC)")
    args = parser.parse_args(argv)
    return run_monthly(args.db, args.as_of)


if __name__ == "__main__":
    raise SystemExit(main())
