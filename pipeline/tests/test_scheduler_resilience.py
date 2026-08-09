"""Proof for the Aug 9 fix: configurable timeout, a heartbeat that fires
WHILE a stage is still running (not only at stage boundaries), the timeout
path actually kills the whole process tree (not just the immediate child --
an orphaned grandchild is exactly how the abandoned pipeline_runs row
happened), and abandoned 'running' rows get corrected on the next start.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from scheduler import common as C  # noqa: E402

# A script that spawns a CHILD of its own (a grandchild relative to
# run_stage), which writes a heartbeat file every 0.2s forever -- proves
# _kill_tree gets the grandchild too, not just the process run_stage
# directly launched.
_SPAWN_GRANDCHILD_SCRIPT = """
import subprocess, sys, time

marker = sys.argv[1]
grandchild_code = (
    "import time,pathlib,sys\\n"
    "m = pathlib.Path(sys.argv[1])\\n"
    "while True:\\n"
    "    m.write_text(str(time.time()))\\n"
    "    time.sleep(0.2)\\n"
)
subprocess.Popen([sys.executable, "-c", grandchild_code, marker])
time.sleep(120)
"""


def test_run_stage_respects_a_short_timeout_and_reports_it(tmp_path):
    result = C.run_stage(
        "sleeper", ["-c", "import time; time.sleep(30)"], timeout=1,
    )
    assert not result.ok
    assert "timed out after 1s" in result.error


def test_run_stage_timeout_kills_the_whole_process_tree(tmp_path):
    script_path = tmp_path / "spawn_grandchild.py"
    script_path.write_text(_SPAWN_GRANDCHILD_SCRIPT, encoding="utf-8")
    marker = tmp_path / "heartbeat.txt"

    result = C.run_stage("tree", [str(script_path), str(marker)], timeout=2)
    assert not result.ok
    assert "timed out" in result.error

    # The grandchild was still writing the marker right up until the kill.
    # If _kill_tree only killed the immediate child (the old proc.kill()
    # behavior), the grandchild would keep writing forever.
    assert marker.exists(), "grandchild never even started writing"
    last_write = marker.read_text()
    time.sleep(1.0)
    assert marker.read_text() == last_write, (
        "marker file still changing after the timeout kill -- a descendant "
        "process survived, the process TREE was not actually killed"
    )


def test_run_stage_completes_normally_well_under_timeout():
    result = C.run_stage("quick", ["-c", "print('hi')"], timeout=30)
    assert result.ok
    assert result.returncode == 0
    assert "hi" in result.stdout_tail


def test_run_stage_heartbeat_fires_while_still_running(monkeypatch):
    monkeypatch.setattr(C, "HEARTBEAT_INTERVAL_SECONDS", 0.3)
    ticks = []

    result = C.run_stage(
        "slow", ["-c", "import time; time.sleep(1.2)"],
        timeout=10, on_heartbeat=lambda elapsed: ticks.append(elapsed),
    )
    assert result.ok
    assert len(ticks) >= 2, f"expected multiple heartbeats during a 1.2s run, got {ticks}"


def test_run_stage_never_heartbeats_after_it_already_finished(monkeypatch):
    monkeypatch.setattr(C, "HEARTBEAT_INTERVAL_SECONDS", 5)
    ticks = []
    C.run_stage(
        "fast", ["-c", "pass"], timeout=30, on_heartbeat=lambda elapsed: ticks.append(elapsed),
    )
    assert ticks == [], "a stage that finishes before one heartbeat interval should get none"


# --------------------------------------------------------- abandoned runs


def _build_db(path: Path):
    conn = migrate.connect(path)
    migrations_dir = PIPELINE_DIR.parent / "migrations"
    for f in sorted(migrations_dir.glob("*.up.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    return conn


def test_reconcile_corrects_an_old_stuck_running_row(tmp_path):
    conn = _build_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status) "
        "VALUES ('r1', 'orchestrate_form4', '2020-01-01T00:00:00Z', 'running')"
    )
    conn.commit()

    corrected = C.reconcile_abandoned_runs(conn, threshold_seconds=3600)
    assert corrected == ["r1"]

    row = conn.execute("SELECT status, finished_at, errors_json FROM pipeline_runs WHERE run_id='r1'").fetchone()
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert "abandoned" in row["errors_json"]


def test_reconcile_leaves_a_recent_running_row_alone(tmp_path):
    from datetime import datetime, timezone

    conn = _build_db(tmp_path / "test.db")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status) "
        "VALUES ('r2', 'orchestrate_form4', ?, 'running')",
        (now,),
    )
    conn.commit()

    corrected = C.reconcile_abandoned_runs(conn, threshold_seconds=3600)
    assert corrected == []
    row = conn.execute("SELECT status FROM pipeline_runs WHERE run_id='r2'").fetchone()
    assert row["status"] == "running"


def test_reconcile_leaves_already_closed_rows_alone(tmp_path):
    conn = _build_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status) "
        "VALUES ('r3', 'scoring', '2020-01-01T00:00:00Z', '2020-01-01T00:05:00Z', 'success')"
    )
    conn.commit()

    corrected = C.reconcile_abandoned_runs(conn, threshold_seconds=3600)
    assert corrected == []
