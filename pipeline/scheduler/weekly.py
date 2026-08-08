"""S6 weekly job: official research-candidate selection, after the final
regular session of the US trading week.

Runs pipeline/selection/compute.py exactly as a human would (no --pool, no
--provisional-threshold -- an official run). "Fires exactly once per trading
week" is enforced two layers down, not by this script:

  - trading_calendar.latest_complete_week refuses an incomplete week, so
    running this a day early is a no-op, not an early selection.
  - compute.py's candidate_id is deterministic per (security, week, ...), so
    re-running an already-selected week cannot duplicate it -- see
    test_candidate_id_is_deterministic_so_a_rerun_cannot_duplicate.

This script's only added job is logging the outcome (published vs. blocked
by a stale required source) and missed-run detection.
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
    missed_run_dates,
    record_scheduler_run,
    run_stage,
    utc_today,
    RunLog,
)

JOB = "weekly"
MISSED_RUN_PERIOD_DAYS = 7


def _log_missed_runs(conn, log: RunLog, today: date) -> list[str]:
    dates = existing_log_dates(JOB)
    last = dates[-1] if dates else None
    missed = missed_run_dates(last, today, MISSED_RUN_PERIOD_DAYS)
    if missed:
        log.section("MISSED RUNS DETECTED")
        for d in missed:
            log.line(f"  no {JOB} log found near {d.isoformat()}")
        errors = [f"missed {JOB} run near: {d.isoformat()}" for d in missed]
        record_scheduler_run(conn, f"{JOB}_missed", "failed", 0, errors)
        return errors
    return []


def _log_outcome(conn, log: RunLog) -> tuple[int, bool]:
    """(candidates written by the newest selection run, whether it was
    blocked by a stale required source)."""
    run = conn.execute(
        "SELECT run_id, status, records_written FROM pipeline_runs "
        "WHERE stage = 'selection' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if run is None:
        log.line("  no selection run found")
        return 0, False

    stale = conn.execute(
        "SELECT COUNT(*) AS n FROM suppressed_signals "
        "WHERE run_id = ? AND suppression_reason = 'stale_source'",
        (run["run_id"],),
    ).fetchone()["n"]
    written = run["records_written"] or 0
    blocked = written == 0 and stale > 0

    log.section("outcome")
    log.line(f"  run_id={run['run_id']} status={run['status']} candidates={written}")
    if blocked:
        detail_row = conn.execute(
            "SELECT detail FROM suppressed_signals WHERE run_id = ? "
            "AND suppression_reason = 'stale_source' LIMIT 1",
            (run["run_id"],),
        ).fetchone()
        log.line(
            "  BLOCKED: a required source failed its freshness check, so no new "
            "candidates were generated. The last published screener (an earlier "
            "successful run) is preserved and shown as stale on /candidates."
        )
        log.line(f"  reason: {detail_row['detail'] if detail_row else '(no detail)'}")
    elif written == 0:
        log.line("  0 candidates -- a legitimate outcome (everything considered "
                  "was suppressed on its own merits, not by a source failure)")
    return written, blocked


def run_weekly(db: str, today: str | None = None) -> int:
    today = today or utc_today()
    log = RunLog(JOB, today)
    conn = migrate.connect(Path(db))

    missed_errors = _log_missed_runs(conn, log, date.fromisoformat(today))

    log.section("stage: selection")
    result = run_stage(
        "selection",
        ["pipeline/selection/compute.py", "--as-of", today, "--db", db],
    )
    log.line(f"  ok={result.ok} returncode={result.returncode}")
    if result.stdout_tail:
        log.line("  --- stdout (tail) ---")
        log.line(result.stdout_tail)
    if not result.ok and result.stderr_tail:
        log.line("  --- stderr (tail) ---")
        log.line(result.stderr_tail)

    written, blocked = _log_outcome(conn, log) if result.ok else (0, False)

    log_path = log.write()

    status = "success" if result.ok and not missed_errors else (
        "failed" if not result.ok else "partial"
    )
    errors = missed_errors + ([] if result.ok else ["selection stage failed"])
    record_scheduler_run(conn, JOB, status, written, errors)

    conn.close()
    print(f"weekly run {today}: status={status} candidates={written} "
          f"blocked_by_stale_source={blocked} log={log_path}")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S6 weekly scheduled run")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=None, help="defaults to today (UTC)")
    args = parser.parse_args(argv)
    return run_weekly(args.db, args.as_of)


if __name__ == "__main__":
    raise SystemExit(main())
