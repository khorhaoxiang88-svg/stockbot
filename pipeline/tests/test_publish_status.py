"""Tests for the public-safe status export.

Two tiers: pure-function tests (compute_status's field math, no git), and
an end-to-end worktree/git test using a temp bare repo standing in for
GitHub -- proves the actual commit-and-push mechanism works, not just the
JSON shape.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import pytest  # noqa: E402

import migrate  # noqa: E402
from scheduler import publish_status as ps  # noqa: E402

MIGRATIONS = PIPELINE_DIR.parent / "migrations"


def _build_db(path: Path):
    conn = migrate.connect(path)
    for f in sorted(MIGRATIONS.glob("*.up.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    return conn


# --------------------------------------------------------------- pure logic


def test_next_scheduled_run_picks_the_soonest_of_the_three():
    now = datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc)  # Sunday
    result = ps.next_scheduled_run(now)
    # Daily fires at 23:00 UTC every day -- same day, 23:00, is soonest.
    assert result == "2026-08-09T23:00:00Z"


def test_next_scheduled_run_is_always_strictly_in_the_future():
    now = datetime(2026, 8, 9, 23, 0, tzinfo=timezone.utc)  # exactly a trigger hour
    result_dt = datetime.strptime(ps.next_scheduled_run(now), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert result_dt > now


def test_meaningfully_changed_ignores_timestamp_only_fields():
    a = {"scanner_state": "running", "generated_at": "t1", "last_activity_at": "t1",
         "next_scheduled_run": "t1", "current_stage": "prices"}
    b = {**a, "generated_at": "t2", "last_activity_at": "t2", "next_scheduled_run": "t9"}
    assert not ps._meaningfully_changed(a, b)


def test_meaningfully_changed_detects_a_real_state_change():
    a = {"scanner_state": "running", "current_stage": "prices"}
    b = {"scanner_state": "running", "current_stage": "form4"}
    assert ps._meaningfully_changed(a, b)


def test_meaningfully_changed_with_no_previous_is_always_true():
    assert ps._meaningfully_changed(None, {"scanner_state": "idle"})


def test_compute_status_reports_no_run_yet_when_selection_never_ran(tmp_path):
    conn = _build_db(tmp_path / "test.db")
    status = ps.compute_status(conn, ps.RunContext(scanner_state="idle"))
    conn.close()

    assert status["scanner_state"] == "idle"
    assert status["current_stage"] is None
    assert status["progress"] is None
    assert status["latest_selection_status"] == "no_run_yet"
    assert status["selected_count"] == 0
    assert status["ranked_count"] == 0
    assert status["latest_score_date"] is None
    # exact field set, nothing extra sensitive ever added by accident
    assert set(status) == {
        "schema_version", "generated_at", "scanner_state", "current_stage",
        "progress", "last_activity_at", "last_success_at", "latest_score_date",
        "ranked_count", "latest_selection_status", "selected_count",
        "next_scheduled_run",
    }


def test_compute_status_reflects_a_published_selection(tmp_path):
    conn = _build_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status, "
        "records_written) VALUES ('run-1', 'selection', 't', 't', 'success', 3)"
    )
    conn.commit()

    status = ps.compute_status(conn, ps.RunContext(scanner_state="succeeded"))
    conn.close()

    assert status["latest_selection_status"] == "published"
    assert status["selected_count"] == 3


def test_compute_status_reflects_zero_candidates_blocked_by_stale_source(tmp_path):
    conn = _build_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO securities (cik, name, classification_source, first_seen, last_seen) "
        "VALUES ('0000000001', 'Test Co', 'test', 't', 't')"
    )
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, finished_at, status, "
        "records_written) VALUES ('run-1', 'selection', 't', 't', 'partial', 0)"
    )
    conn.execute(
        "INSERT INTO suppressed_signals (run_id, security_id, horizon_days, "
        "suppression_reason, detail) VALUES "
        "('run-1', 1, 20, 'stale_source', 'price ingest stale')"
    )
    conn.commit()

    status = ps.compute_status(conn, ps.RunContext(scanner_state="succeeded"))
    conn.close()

    assert status["latest_selection_status"] == "blocked_stale_source"


def test_no_field_ever_contains_a_symbol_price_or_path():
    """Cheap but real guard: the payload's own JSON text, dumped, must never
    contain anything shaped like a file path or a dollar-price -- catches a
    future accidental field addition leaking something it shouldn't."""
    status = {
        "schema_version": 1, "generated_at": "2026-08-09T00:00:00Z",
        "scanner_state": "idle", "current_stage": None, "progress": None,
        "last_activity_at": "2026-08-09T00:00:00Z", "last_success_at": None,
        "latest_score_date": "2026-08-03", "ranked_count": 97,
        "latest_selection_status": "no_run_yet", "selected_count": 0,
        "next_scheduled_run": "2026-08-09T23:00:00Z",
    }
    text = json.dumps(status)
    assert "C:\\" not in text and "/Users/" not in text
    assert "sk-" not in text and "api_key" not in text.lower()


# --------------------------------------------------------------- git worktree


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A real temp git repo standing in for REPO_ROOT, with a bare repo
    standing in for GitHub as 'origin' -- proves ensure_worktree()/publish()
    actually create the branch and push, without touching the real repo or
    the real network."""
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    origin.mkdir()
    repo.mkdir()

    subprocess.run(["git", "init", "--bare"], cwd=origin, check=True, capture_output=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:master"], cwd=repo, check=True, capture_output=True)

    worktree_dir = tmp_path / "bot-status-worktree"
    monkeypatch.setattr(ps, "REPO_ROOT", repo)
    monkeypatch.setattr(ps, "WORKTREE_DIR", worktree_dir)
    monkeypatch.setattr(ps, "HEARTBEAT_SECONDS", 0)
    return repo, origin, worktree_dir


def _status(state="running", stage="prices"):
    return {
        "schema_version": 1, "generated_at": "2026-08-09T00:00:00Z",
        "scanner_state": state, "current_stage": stage, "progress": None,
        "last_activity_at": "2026-08-09T00:00:00Z", "last_success_at": None,
        "latest_score_date": None, "ranked_count": 0,
        "latest_selection_status": "no_run_yet", "selected_count": 0,
        "next_scheduled_run": "2026-08-09T23:00:00Z",
    }


def test_publish_creates_the_orphan_branch_and_pushes_it(fake_repo):
    repo, origin, worktree_dir = fake_repo

    pushed = ps.publish(_status(), force=True)

    assert pushed
    assert (worktree_dir / "status.json").exists()
    written = json.loads((worktree_dir / "status.json").read_text(encoding="utf-8"))
    assert written["scanner_state"] == "running"

    branches = subprocess.run(
        ["git", "branch", "-a"], cwd=origin, capture_output=True, text=True
    ).stdout
    assert "bot-status" in branches
    # the orphan branch must NOT contain the main repo's README
    assert not (worktree_dir / "README.md").exists()


def test_publish_skips_an_identical_republish(fake_repo, monkeypatch):
    repo, origin, worktree_dir = fake_repo
    monkeypatch.setattr(ps, "HEARTBEAT_SECONDS", 999999)

    assert ps.publish(_status(), force=True)
    assert not ps.publish(_status())  # nothing changed, heartbeat not elapsed


def test_publish_pushes_again_when_the_state_actually_changes(fake_repo):
    repo, origin, worktree_dir = fake_repo

    assert ps.publish(_status(state="running"), force=True)
    assert ps.publish(_status(state="succeeded"))
    written = json.loads((worktree_dir / "status.json").read_text(encoding="utf-8"))
    assert written["scanner_state"] == "succeeded"


def test_publish_heartbeats_even_with_no_change_once_interval_elapses(fake_repo, monkeypatch):
    repo, origin, worktree_dir = fake_repo
    assert ps.publish(_status(), force=True)

    # Same scanner_state/stage (no "meaningful" change) but a later
    # generated_at/last_activity_at -- exactly what a real heartbeat looks
    # like: state unchanged, but time (and therefore the file bytes) moved.
    later = {**_status(), "generated_at": "2026-08-09T00:05:00Z",
             "last_activity_at": "2026-08-09T00:05:00Z"}
    monkeypatch.setattr(ps, "_seconds_since_last_commit", lambda w: 10_000)
    assert ps.publish(later)  # heartbeat interval elapsed -> pushes despite no real change
