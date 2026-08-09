"""Proof that two scheduler jobs cannot hold the lock at the same time, and
that a stale lock (crashed process, never released) does not wedge every
future run forever.

RETRY_ATTEMPTS/RETRY_DELAY_SECONDS/STALE_AFTER_SECONDS are monkeypatched down
so this runs in under a second -- the real values (6 attempts x 10s, 4h
staleness) are chosen for production pacing, not test speed.
"""

from __future__ import annotations

import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import pytest  # noqa: E402

from scheduler import lock as lockmod  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(lockmod, "LOCK_PATH", tmp_path / "scheduler.lock")
    monkeypatch.setattr(lockmod, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(lockmod, "RETRY_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(lockmod, "STALE_AFTER_SECONDS", 3600)


def test_a_second_job_cannot_acquire_while_the_first_holds_it():
    with lockmod.scheduler_lock("daily") as first:
        assert first.acquired
        with lockmod.scheduler_lock("weekly") as second:
            assert not second.acquired
            assert "daily" in second.held_by


def test_the_lock_is_released_when_the_first_job_exits_its_with_block():
    with lockmod.scheduler_lock("daily") as first:
        assert first.acquired
    with lockmod.scheduler_lock("weekly") as second:
        assert second.acquired


def test_the_lock_is_released_even_if_the_holder_raises():
    with pytest.raises(RuntimeError):
        with lockmod.scheduler_lock("daily") as first:
            assert first.acquired
            raise RuntimeError("simulated stage crash")
    with lockmod.scheduler_lock("weekly") as second:
        assert second.acquired, "a raised exception inside the lock must not leak it"


def test_three_overlapping_attempts_only_one_succeeds_at_a_time():
    with lockmod.scheduler_lock("daily") as a:
        assert a.acquired
        with lockmod.scheduler_lock("weekly") as b:
            assert not b.acquired
            with lockmod.scheduler_lock("monthly") as c:
                assert not c.acquired


def test_a_stale_lock_from_a_crashed_process_is_reclaimed(monkeypatch):
    monkeypatch.setattr(lockmod, "STALE_AFTER_SECONDS", 0)
    # Simulate a job that acquired the lock and never released it (crash).
    lockmod.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lockmod.LOCK_PATH.write_text("daily pid=999999 acquired_at=0\n", encoding="utf-8")

    with lockmod.scheduler_lock("weekly") as recovered:
        assert recovered.acquired, "a lock older than STALE_AFTER_SECONDS must be reclaimable"


def test_a_fresh_lock_is_never_reclaimed_as_stale(monkeypatch):
    monkeypatch.setattr(lockmod, "STALE_AFTER_SECONDS", 3600)
    with lockmod.scheduler_lock("daily") as first:
        assert first.acquired
        with lockmod.scheduler_lock("weekly") as second:
            assert not second.acquired, "a lock well under the staleness threshold must hold"
