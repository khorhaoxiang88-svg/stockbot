"""Shared plumbing for the S6 schedulers (daily.py, weekly.py, monthly.py).

No pipeline logic lives here. Every stage a scheduler runs is the SAME CLI
script a human already runs by hand (see README's manual-run section) --
this module only adds what running several of those unattended, in sequence,
needs on top: per-stage isolation (one stage's failure must not stop the
rest), a plain-text daily/weekly/monthly log, a pipeline_runs row so each
scheduler invocation shows up in /health's run history the same way any
other stage already does, and missed-run detection.

pipeline_runs.status is CHECK-constrained to ('running', 'success', 'failed',
'partial') (migration 001) -- a missed run is recorded as 'failed' rather
than inventing a fifth status value that would need a table rebuild to add.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"
LOG_DIR = REPO_ROOT / "data" / "logs"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def latest_pool_version(conn) -> str | None:
    """The most recently loaded universe_candidate_pool version.

    discovered_at, not the version string, decides "most recent" -- version
    strings are free-form labels (s1-sample-v1, s2-slice-v1, ...), not a
    sortable sequence.
    """
    row = conn.execute(
        "SELECT pool_version FROM universe_candidate_pool "
        "ORDER BY discovered_at DESC LIMIT 1"
    ).fetchone()
    return row["pool_version"] if row else None


@dataclass
class StageResult:
    name: str
    ok: bool
    returncode: int | None
    started_at: str
    finished_at: str
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None


def _tail(text: str, lines: int = 25) -> str:
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def run_stage(name: str, args: list[str], cwd: Path = REPO_ROOT) -> StageResult:
    """Run one pipeline CLI as a subprocess and ALWAYS return a StageResult --
    never raise. This is the isolation boundary: a scheduler loops over
    several of these and a failure in one (non-zero exit, or the subprocess
    could not even start) must not stop the loop from reaching the rest.
    """
    started = utc_now_iso()
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        return StageResult(
            name=name,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            started_at=started,
            finished_at=utc_now_iso(),
            stdout_tail=_tail(proc.stdout),
            stderr_tail=_tail(proc.stderr),
        )
    except Exception as exc:  # noqa: BLE001 -- isolation boundary, must not raise
        return StageResult(
            name=name,
            ok=False,
            returncode=None,
            started_at=started,
            finished_at=utc_now_iso(),
            error=str(exc),
        )


@dataclass
class RunLog:
    job: str
    key: str  # date (daily), ISO week (weekly), or year-month (monthly)
    lines: list[str] = field(default_factory=list)

    def section(self, title: str) -> None:
        self.lines.append("")
        self.lines.append(f"=== {title} ===")

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def path(self) -> Path:
        return LOG_DIR / f"{self.job}-{self.key}.log"

    def write(self) -> Path:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = self.path()
        header = f"stockbot {self.job} run -- {self.key} -- written {utc_now_iso()}"
        out.write_text(header + "\n" + "\n".join(self.lines) + "\n", encoding="utf-8")
        return out


def record_scheduler_run(
    conn, job: str, status: str, records_written: int, errors: list[str]
) -> str:
    """One pipeline_runs row per scheduler invocation, stage='scheduler_<job>'.

    This is the only piece of "run history" a scheduler needs to add: every
    individual stage (prices, form4, scoring, ...) already writes its own
    pipeline_runs row, and /health's run history already reads the whole
    table (web/lib/db.ts getRecentRuns). This row is what lets a scheduler
    invocation itself -- and a missed one -- show up there too.
    """
    import json

    run_id = f"scheduler-{job}-{uuid.uuid4().hex[:12]}"
    now = utc_now_iso()
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status, "
        "records_written, code_version, errors_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            f"scheduler_{job}",
            now,
            now,
            status,
            records_written,
            "s6-scheduler/v1",
            json.dumps(errors) if errors else None,
        ),
    )
    conn.execute("COMMIT")
    return run_id


def existing_log_dates(job: str, log_dir: Path = LOG_DIR) -> list[date]:
    """Dates parsed from this job's existing log filenames, oldest first.
    Weekly/monthly logs use non-date keys (ISO week, year-month) and are
    parsed by their own callers -- this is the daily-log (YYYY-MM-DD) case.
    """
    if not log_dir.exists():
        return []
    found = []
    prefix = f"{job}-"
    for p in log_dir.glob(f"{prefix}*.log"):
        key = p.stem[len(prefix):]
        try:
            found.append(date.fromisoformat(key))
        except ValueError:
            continue
    return sorted(found)


def missed_run_dates(
    last_run_date: date | None, today: date, period_days: int, grace_days: int = 0
) -> list[date]:
    """Dates a scheduled run should have happened (spaced period_days apart
    from the last one seen) but has no log for, strictly before today.

    Only the trailing gap since the last recorded run is checked -- this
    answers "did we just miss one", not "audit all of history" every time it
    runs. If there is no prior run at all, nothing is reported: a brand new
    job has nothing to have missed yet.
    """
    if last_run_date is None:
        return []
    gap = (today - last_run_date).days
    if gap <= period_days + grace_days:
        return []
    missed = []
    d = last_run_date + timedelta(days=period_days)
    while d < today:
        missed.append(d)
        d += timedelta(days=period_days)
    return missed
