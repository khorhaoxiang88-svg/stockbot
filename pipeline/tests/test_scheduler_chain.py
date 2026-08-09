"""Proof that daily.py chains weekly.py after it finishes -- the actual
delivery mechanism for weekly selection, not the standalone Saturday
trigger, which is a fallback (see weekly.py's module docstring)."""

from __future__ import annotations

import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from scheduler import daily as dailymod  # noqa: E402


def test_chain_weekly_invokes_weekly_py_with_the_same_db_and_date(monkeypatch):
    calls = []

    def fake_run_stage(name, args, cwd=None):
        calls.append((name, args))

        class _Result:
            ok = True
            returncode = 0
            stdout_tail = ""
            stderr_tail = ""

        return _Result()

    monkeypatch.setattr(dailymod, "run_stage", fake_run_stage)
    dailymod._chain_weekly("data/example.db", "2026-08-09")

    assert len(calls) == 1
    name, args = calls[0]
    assert name == "chained_weekly"
    assert args == [
        "pipeline/scheduler/weekly.py", "--db", "data/example.db",
        "--as-of", "2026-08-09",
    ]


def test_a_failed_chained_weekly_does_not_raise(monkeypatch):
    def fake_run_stage(name, args, cwd=None):
        class _Result:
            ok = False
            returncode = 1
            stdout_tail = ""
            stderr_tail = "boom"

        return _Result()

    monkeypatch.setattr(dailymod, "run_stage", fake_run_stage)
    # Must not raise -- a chained-weekly failure is isolated, same principle
    # run_stage already applies to every other stage within one run.
    dailymod._chain_weekly("data/example.db", "2026-08-09")


def test_main_calls_run_daily_then_chains_weekly_by_default(monkeypatch):
    order = []
    monkeypatch.setattr(dailymod, "run_daily", lambda db, today, *a, **k: order.append("daily") or 0)
    monkeypatch.setattr(dailymod, "_chain_weekly", lambda db, today: order.append("weekly"))

    code = dailymod.main(["--db", "data/example.db", "--as-of", "2026-08-09"])

    assert code == 0
    assert order == ["daily", "weekly"]


def test_no_chain_weekly_flag_skips_the_chain(monkeypatch):
    order = []
    monkeypatch.setattr(dailymod, "run_daily", lambda db, today, *a, **k: order.append("daily") or 0)
    monkeypatch.setattr(dailymod, "_chain_weekly", lambda db, today: order.append("weekly"))

    dailymod.main(["--db", "data/example.db", "--as-of", "2026-08-09", "--no-chain-weekly"])

    assert order == ["daily"]
