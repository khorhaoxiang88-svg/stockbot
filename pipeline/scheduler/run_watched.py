"""Watchdog wrapper for the S6 schedulers -- this is what the Windows
Scheduled Tasks actually invoke now, not daily.py/weekly.py/monthly.py
directly.

Why: Task Scheduler stops a task by sending CTRL_BREAK_EVENT to the
process's console group. Python's default handling of that is an abrupt
process exit with no chance for the child to run its own closing
publish_status call -- exactly what happened Aug 9 (exit code
-1073741510 / STATUS_CONTROL_C_EXIT): the pipeline_runs row and the public
status.json both stayed on stale "running" data for hours after the
process had actually died.

This wrapper launches the real scheduler script as a child and installs a
signal handler for SIGBREAK (Windows) / SIGTERM (POSIX). If EITHER fires,
it immediately publishes scanner_state='failed' (best-effort, must never
itself raise) before terminating the child, so the public status reflects
reality within seconds of the kill rather than staying wrong indefinitely.

Deliberately does NOT publish on an ordinary non-zero exit code -- daily.py/
weekly.py/monthly.py already call publish_for with an accurate status
(succeeded/partial/failed) as the last thing they do before returning
normally; only an externally-triggered signal is a case they had no chance
to react to themselves.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from scheduler.publish_status import RunContext, publish_for  # noqa: E402


def _publish_failed(db_path: str, reason: str) -> None:
    try:
        conn = migrate.connect(Path(db_path))
        publish_for(conn, RunContext(scanner_state="failed", current_stage=None), force=True)
        conn.close()
    except Exception:  # noqa: BLE001 -- a signal handler must never raise
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: run_watched.py <script.py> [args...]", file=sys.stderr)
        return 2

    target, rest = argv[0], argv[1:]
    db_path = str(migrate.DEFAULT_DB_PATH)
    if "--db" in rest:
        db_path = rest[rest.index("--db") + 1]

    proc = subprocess.Popen([sys.executable, target, *rest], cwd=str(PIPELINE_DIR.parent))

    terminated_externally = {"flag": False}

    def _on_signal(signum, _frame):
        terminated_externally["flag"] = True
        _publish_failed(db_path, f"scheduler terminated externally (signal {signum})")
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    returncode = proc.wait()
    return 1 if terminated_externally["flag"] else returncode


if __name__ == "__main__":
    raise SystemExit(main())
