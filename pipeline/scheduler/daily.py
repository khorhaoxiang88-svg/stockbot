"""S6 daily job: everything the brief's DAILY schedule names, after the US
market close --

  price ingestion, Form 4 ingestion, scheduled XBRL refresh, scoring,
  risk flags, open-position monitoring and exit evaluation, data health,
  severe new risk flag logging

-- each stage is the same CLI a human already runs by hand (see README);
this script only sequences them, isolates one stage's failure from the
rest (scheduler.common.run_stage never raises), and writes the daily log.

Deliberately NOT run daily: dilution. riskflags/compute.py reads the latest
dilution_signals row as of the cutoff date, not strictly today's -- an older
row degrades gracefully rather than being treated as missing (selection
already excludes securities with no row at all, see S3). The brief's daily
list does not name it either. Refreshing it is a scope decision for whoever
schedules dilution's own cadence, not something to fold in here silently.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import load_config  # noqa: E402
from scheduler.common import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    LOG_DIR,
    REPO_ROOT,
    RunLog,
    existing_log_dates,
    latest_pool_version,
    missed_run_dates,
    reconcile_abandoned_runs,
    record_scheduler_run,
    run_stage,
    utc_today,
)
from scheduler.lock import scheduler_lock  # noqa: E402
from scheduler.publish_status import RunContext, publish_for  # noqa: E402
from selection import freshness as FR  # noqa: E402

# daily.py's own stage names -> the DB's orchestrate stage name, for the
# per-item progress lookup in publish_status. Stages not listed here have no
# per-item concept (fundamentals/scoring/execution run once, not per-security).
ORCHESTRATE_STAGE_NAMES = {"prices": "prices", "form4": "form4", "xbrl": "xbrl", "riskflags": "riskflags"}

JOB = "daily"
MISSED_RUN_PERIOD_DAYS = 1

# The Aug 9 form4 run needed longer than the old blanket 3600s (1h) --
# 469 of ~937 securities completed before the timeout killed it. Orchestrate
# stages scale with the whole universe and legitimately need hours (README's
# own S2 history: up to ~4h20m for a comparable batch); the non-orchestrate
# stages (fundamentals/scoring/execution, one pass, not per-security) never
# have, so they keep the shorter default rather than getting a blanket bump.
DEFAULT_ORCHESTRATE_TIMEOUT_SECONDS = 6 * 3600


def _stage_specs(pool: str | None, today: str, db: str) -> list[tuple[str, list[str]]]:
    common = ["--db", db]
    pool_args = ["--pool", pool] if pool else []
    return [
        ("prices", ["pipeline/orchestrate/run.py", "--tier", "prices", *pool_args,
                    "--batch-id", f"prices-{today}", *common]),
        ("form4", ["pipeline/orchestrate/run.py", "--tier", "form4", *pool_args,
                   "--batch-id", f"form4-{today}", *common]),
        ("xbrl", ["pipeline/orchestrate/run.py", "--tier", "xbrl", *pool_args,
                  "--batch-id", f"xbrl-{today}", *common]),
        ("fundamentals", ["pipeline/fundamentals/compute.py", *pool_args, *common]),
        ("scoring", ["pipeline/scoring/compute.py", *pool_args, "--as-of", today, *common]),
        ("riskflags", ["pipeline/orchestrate/run.py", "--tier", "riskflags", *pool_args,
                        "--as-of", today, "--batch-id", f"riskflags-{today}", *common]),
        ("execution", ["pipeline/execution/compute.py", "--as-of", today, *common]),
    ]


def _log_missed_runs(conn, log: RunLog, today: date) -> list[str]:
    dates = existing_log_dates(JOB)
    last = dates[-1] if dates else None
    missed = missed_run_dates(last, today, MISSED_RUN_PERIOD_DAYS)
    if missed:
        log.section("MISSED RUNS DETECTED")
        for d in missed:
            log.line(f"  no {JOB} log found for {d.isoformat()}")
        errors = [f"missed {JOB} run: {d.isoformat()}" for d in missed]
        record_scheduler_run(conn, f"{JOB}_missed", "failed", 0, errors)
        return errors
    return []


def _log_data_health(conn, cfg, log: RunLog, today: str) -> None:
    log.section("data health")
    cutoff_utc = f"{today}T23:59:59Z"
    now_utc = cutoff_utc
    report = FR.check_pipeline_freshness(conn, cutoff_utc, now_utc, cfg)
    for status in report.statuses:
        log.line(f"  {'ok  ' if status.ok else 'FAIL'} {status.source}: {status.detail}")
    log.line(f"  overall: {'ok' if report.ok else 'DEGRADED'}")

    log.section("source health snapshot")
    for row in conn.execute(
        "SELECT source_name, last_success, last_error, consecutive_failures, "
        "staleness_hours, coverage_pct FROM source_health ORDER BY source_name"
    ):
        flag = " *** " if row["consecutive_failures"] and row["consecutive_failures"] > 0 else "     "
        log.line(
            f"{flag}{row['source_name']}: last_success={row['last_success']} "
            f"consecutive_failures={row['consecutive_failures']} "
            f"staleness_hours={row['staleness_hours']} coverage_pct={row['coverage_pct']}"
        )


def _log_severe_risk_flags(conn, log: RunLog, today: str) -> int:
    log.section("severe new risk flags")
    rows = conn.execute(
        "SELECT rf.security_id, l.symbol, rf.flag_code, rf.evidence_text "
        "FROM risk_flags rf "
        "LEFT JOIN listings l ON l.security_id = rf.security_id AND l.valid_to IS NULL "
        "WHERE rf.as_of_date = ? AND rf.severity = 'high' "
        "ORDER BY rf.security_id",
        (today,),
    ).fetchall()
    if not rows:
        log.line("  none")
        return 0
    for row in rows:
        symbol = row["symbol"] or row["security_id"]
        log.line(f"  SEVERE  {symbol}  {row['flag_code']}  {row['evidence_text']}")
    return len(rows)


def _log_positions(conn, log: RunLog, today: str) -> None:
    # paper_positions has no security_id column of its own -- it is reached
    # through the candidate it was opened from (matches web/lib/db.ts's own
    # JOIN research_candidates c ON c.candidate_id = p.candidate_id).
    opened = conn.execute(
        "SELECT p.position_id, c.security_id, p.book_id FROM paper_positions p "
        "JOIN research_candidates c ON c.candidate_id = p.candidate_id "
        "WHERE p.entry_date = ?",
        (today,),
    ).fetchall()
    closed = conn.execute(
        "SELECT p.position_id, c.security_id, p.exit_reason FROM paper_positions p "
        "JOIN research_candidates c ON c.candidate_id = p.candidate_id "
        "WHERE p.exit_date = ?",
        (today,),
    ).fetchall()
    pending = conn.execute(
        "SELECT p.position_id, c.security_id FROM paper_positions p "
        "JOIN research_candidates c ON c.candidate_id = p.candidate_id "
        "WHERE p.status = 'pending_resolution'"
    ).fetchall()

    log.section("positions opened today")
    if not opened:
        log.line("  0 opened")
    else:
        for r in opened:
            log.line(f"  {r['position_id']} security={r['security_id']} book={r['book_id']}")

    log.section("positions closed today")
    if not closed:
        log.line("  0 closed")
    else:
        for r in closed:
            log.line(f"  {r['position_id']} security={r['security_id']} reason={r['exit_reason']}")

    log.section("pending resolutions")
    if not pending:
        log.line("  none")
    else:
        for r in pending:
            log.line(f"  {r['position_id']} security={r['security_id']}")


def run_daily(db: str, today: str | None = None, orchestrate_timeout: int = DEFAULT_ORCHESTRATE_TIMEOUT_SECONDS) -> int:
    today = today or utc_today()
    with scheduler_lock(JOB) as lock:
        if not lock.acquired:
            conn = migrate.connect(Path(db))
            record_scheduler_run(
                conn, JOB, "failed", 0,
                [f"skipped: another Stockbot job is running (lock held by {lock.held_by})"],
            )
            conn.close()
            print(f"daily run {today}: SKIPPED, lock held by {lock.held_by}")
            return 2
        return _run_daily_locked(db, today, orchestrate_timeout)


def _run_daily_locked(db: str, today: str, orchestrate_timeout: int = DEFAULT_ORCHESTRATE_TIMEOUT_SECONDS) -> int:
    log = RunLog(JOB, today)
    conn = migrate.connect(Path(db))
    cfg = load_config()

    corrected = reconcile_abandoned_runs(conn)
    if corrected:
        log.section("reconciled abandoned runs")
        for run_id in corrected:
            log.line(f"  {run_id}: was stuck 'running', corrected to 'failed'")

    missed_errors = _log_missed_runs(conn, log, date.fromisoformat(today))

    pool = latest_pool_version(conn)
    log.section("pool")
    log.line(f"  {pool or '(none loaded -- falling back to fixture securities)'}")

    publish_for(conn, RunContext(scanner_state="running", current_stage=None))

    stage_results = {}
    for name, args in _stage_specs(pool, today, db):
        # Announced before the stage's subprocess runs -- daily.py's own
        # loop is synchronous per stage, so this is stage-boundary
        # granularity, not sub-second intra-stage progress. Documented
        # rather than oversold: progress numbers only advance once a stage
        # actually finishes and its own run_id can be looked up (below).
        publish_for(conn, RunContext(scanner_state="running", current_stage=name))

        db_stage_name = ORCHESTRATE_STAGE_NAMES.get(name)
        stage_timeout = orchestrate_timeout if db_stage_name else DEFAULT_TIMEOUT_SECONDS

        def _heartbeat(elapsed_seconds, _name=name, _db_stage=db_stage_name):
            # Fires every ~60s WHILE the stage subprocess is still running --
            # this is the actual intra-stage progress requirement; the
            # publish_for calls immediately before/after the subprocess call
            # are only stage-boundary announcements, not this.
            run_id = None
            if _db_stage:
                row = conn.execute(
                    "SELECT run_id FROM pipeline_runs WHERE stage = ? "
                    "ORDER BY started_at DESC LIMIT 1",
                    (f"orchestrate_{_db_stage}",),
                ).fetchone()
                run_id = row["run_id"] if row else None
            publish_for(conn, RunContext(scanner_state="running", current_stage=_name, run_id=run_id))

        log.section(f"stage: {name}")
        result = run_stage(name, args, timeout=stage_timeout, on_heartbeat=_heartbeat)
        stage_results[name] = result
        log.line(f"  ok={result.ok} returncode={result.returncode}")
        if result.error:
            log.line(f"  error: {result.error}")
        if result.stdout_tail:
            log.line("  --- stdout (tail) ---")
            log.line(result.stdout_tail)
        if not result.ok and result.stderr_tail:
            log.line("  --- stderr (tail) ---")
            log.line(result.stderr_tail)

        db_stage = ORCHESTRATE_STAGE_NAMES.get(name)
        run_id = None
        if db_stage:
            row = conn.execute(
                "SELECT run_id FROM pipeline_runs WHERE stage = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (f"orchestrate_{db_stage}",),
            ).fetchone()
            run_id = row["run_id"] if row else None
        publish_for(conn, RunContext(scanner_state="running", current_stage=name, run_id=run_id))

    _log_data_health(conn, cfg, log, today)
    severe_count = _log_severe_risk_flags(conn, log, today)
    _log_positions(conn, log, today)

    log_path = log.write()

    failed_stages = [name for name, r in stage_results.items() if not r.ok]
    status = "success" if not failed_stages and not missed_errors else (
        "failed" if len(failed_stages) == len(stage_results) else "partial"
    )
    errors = missed_errors + [f"stage failed: {name}" for name in failed_stages]
    record_scheduler_run(conn, JOB, status, severe_count, errors)

    # 'partial' is its own scanner_state (not one of the 4 named in the
    # original spec, but real -- pipeline_runs.status already distinguishes
    # it, and the public site's amber "stale/partial" color rule expects it
    # to exist), never flattened into a misleadingly-clean "succeeded".
    scanner_state = {"success": "succeeded", "partial": "partial", "failed": "failed"}[status]
    publish_for(conn, RunContext(scanner_state=scanner_state, current_stage=None), force=True)

    conn.close()
    print(f"daily run {today}: status={status} log={log_path}")
    if failed_stages:
        print(f"  failed stages: {', '.join(failed_stages)}")
    return 1 if failed_stages else 0


def _chain_weekly(db: str, today: str) -> None:
    """Attempt weekly selection right after daily finishes, lock released.

    Unconditional, every day -- not just Fridays/Saturdays. weekly.py's own
    trading-week-completeness check (SELECTION-RULE-1.1) already makes this
    a safe no-op on any day the week isn't actually over, same as running it
    by hand early would be (README: "running this a day early is a no-op,
    not an early selection"), and candidate_id's determinism makes re-running
    an already-selected week a no-op too. Reusing those existing guards is
    safer than daily.py re-deriving its own "is today the right day" logic.

    This is the PRIMARY path weekly runs through now. Its own standalone
    Saturday 07:30 scheduled trigger still exists as a fallback (in case a
    daily run never reaches this line -- crash, host powered off) but no
    longer needs to be the only path: if daily runs long into Saturday and
    that trigger finds the lock still held, it correctly records a skip and
    moves on -- because this chain call is what actually delivers the
    selection run, whenever daily finishes, not whichever day that lands on.

    A weekly failure here never changes daily's own exit code -- same
    per-stage isolation principle run_stage already applies within a single
    scheduler run, one level up.
    """
    print(f"chaining weekly selection after daily ({today})...")
    result = run_stage(
        "chained_weekly",
        ["pipeline/scheduler/weekly.py", "--db", db, "--as-of", today],
    )
    print(f"  chained weekly: ok={result.ok} returncode={result.returncode}")
    if result.stdout_tail:
        print(result.stdout_tail)
    if not result.ok and result.stderr_tail:
        print(result.stderr_tail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S6 daily scheduled run")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=None, help="defaults to today (UTC)")
    parser.add_argument(
        "--no-chain-weekly", action="store_true",
        help="skip the automatic weekly-selection chain (for isolated/manual runs)",
    )
    parser.add_argument(
        "--orchestrate-timeout", type=int, default=DEFAULT_ORCHESTRATE_TIMEOUT_SECONDS,
        help=f"per-stage timeout in seconds for the orchestrate tiers (prices/form4/xbrl/"
             f"riskflags), which scale with the whole universe (default "
             f"{DEFAULT_ORCHESTRATE_TIMEOUT_SECONDS}s = 6h)",
    )
    args = parser.parse_args(argv)
    today = args.as_of or utc_today()
    result = run_daily(args.db, today, args.orchestrate_timeout)
    if not args.no_chain_weekly:
        _chain_weekly(args.db, today)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
