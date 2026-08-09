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

import os
import subprocess
import sys
import tempfile
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


DEFAULT_TIMEOUT_SECONDS = 3600
HEARTBEAT_INTERVAL_SECONDS = 60


def _kill_tree(pid: int) -> None:
    """Kills a process AND every descendant it spawned -- plain proc.kill()
    only kills the immediate child. The Aug 9 form4 run's abandoned
    pipeline_runs row (never corrected to 'failed', stuck 'running' for
    hours) is exactly the failure mode an incomplete kill produces: if a
    grandchild survives, nothing ever finishes writing the closing row.

    Windows-first (taskkill /T), since that's this project's actual runtime;
    POSIX path (process-group kill) is a real fallback, not just cargo cult,
    since pytest itself runs cross-platform.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_stage(
    name: str,
    args: list[str],
    cwd: Path = REPO_ROOT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    on_heartbeat=None,
) -> StageResult:
    """Run one pipeline CLI as a subprocess and ALWAYS return a StageResult --
    never raise. This is the isolation boundary: a scheduler loops over
    several of these and a failure in one (non-zero exit, or the subprocess
    could not even start) must not stop the loop from reaching the rest.

    Popen + poll loop, not subprocess.run(timeout=...), so on_heartbeat can
    fire periodically WHILE a slow stage is still running -- the Aug 9 fix
    needed both a longer timeout AND a heartbeat during it, not just one.
    A stdout/stderr tempfile (not PIPE) avoids the classic deadlock a long-
    running process with a lot of output can hit against an unread pipe.
    """
    started = utc_now_iso()
    stdout_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        popen_kwargs = {"cwd": str(cwd), "stdout": stdout_file, "stderr": stderr_file, "text": True}
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True  # own process group, for _kill_tree
        else:
            popen_kwargs["creationflags"] = creationflags

        proc = subprocess.Popen([sys.executable, *args], **popen_kwargs)

        elapsed = 0
        while True:
            returncode = proc.poll()
            if returncode is not None:
                break
            if elapsed >= timeout:
                _kill_tree(proc.pid)
                stdout_file.seek(0)
                stderr_file.seek(0)
                return StageResult(
                    name=name,
                    ok=False,
                    returncode=None,
                    started_at=started,
                    finished_at=utc_now_iso(),
                    stdout_tail=_tail(stdout_file.read()),
                    stderr_tail=_tail(stderr_file.read()),
                    error=f"timed out after {timeout}s, process tree killed",
                )
            step = min(HEARTBEAT_INTERVAL_SECONDS, timeout - elapsed)
            try:
                proc.wait(timeout=step)
            except subprocess.TimeoutExpired:
                pass
            elapsed += step
            if proc.poll() is None and on_heartbeat is not None:
                on_heartbeat(elapsed)

        stdout_file.seek(0)
        stderr_file.seek(0)
        return StageResult(
            name=name,
            ok=returncode == 0,
            returncode=returncode,
            started_at=started,
            finished_at=utc_now_iso(),
            stdout_tail=_tail(stdout_file.read()),
            stderr_tail=_tail(stderr_file.read()),
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
    finally:
        stdout_file.close()
        stderr_file.close()


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


ABANDONED_RUN_THRESHOLD_SECONDS = 8 * 3600  # generous above the 6h max stage timeout


def reconcile_abandoned_runs(conn, threshold_seconds: int = ABANDONED_RUN_THRESHOLD_SECONDS) -> list[str]:
    """Corrects pipeline_runs rows stuck at status='running' from a PRIOR
    invocation whose parent process was killed externally (Task Scheduler
    stop, OS shutdown, crash) before it could write its own closing UPDATE --
    exactly what happened to the Aug 9 form4 run, which sat 'running' with
    finished_at NULL for hours after the actual process had already died.

    Called at the START of daily/weekly/monthly, before anything else, so a
    stale row from last time never gets displayed (via /health or
    publish_status) as though it were still live. Only rows older than
    threshold_seconds are touched -- a genuinely-in-progress row from a
    concurrent run (which the scheduler lock should already prevent, but
    this is a second, independent safety net) is left alone.

    Returns the list of run_ids corrected, for the caller's own log.
    """
    cutoff = utc_now_iso()
    stale = conn.execute(
        "SELECT run_id, stage, started_at FROM pipeline_runs WHERE status = 'running'"
    ).fetchall()
    corrected = []
    for row in stale:
        started = datetime.strptime(row["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - started).total_seconds()
        if age_seconds < threshold_seconds:
            continue
        conn.execute(
            "UPDATE pipeline_runs SET status = 'failed', finished_at = ?, "
            "errors_json = ? WHERE run_id = ?",
            (
                cutoff,
                f'["abandoned: still \'running\' after {age_seconds / 3600:.1f}h, '
                f'parent process was terminated externally before it could record '
                f'its own result"]',
                row["run_id"],
            ),
        )
        corrected.append(row["run_id"])
    if corrected:
        conn.commit()
    return corrected


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
