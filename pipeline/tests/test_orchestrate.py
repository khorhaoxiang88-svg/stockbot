"""S2 scaled ingestion: resumability, the consecutive-failure circuit
breaker, rate limiting, and coverage reconciliation.

Failure is injected deterministically (monkeypatch) rather than by actually
killing an OS process. A real kill test is flaky by nature -- timing-
dependent, and on this platform Git Bash's `kill` does not reliably reach a
Windows python.exe subprocess. Fault injection is what F12's own harness
tests use for the same reason: the property under test is "does a failure
here get handled correctly", and that is exactly as true whether the failure
is a real SIGKILL or a raised exception at the same point.
"""

from __future__ import annotations

import time

import pytest

import migrate
from orchestrate import progress as P
from orchestrate import run as R
from universe import identity


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "orchestrate.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def fake_run(conn, run_id="run-1"):
    """orchestration_progress.run_id has a FK to pipeline_runs; the low-level
    progress.py tests need a real row to point at."""
    conn.execute(
        "INSERT OR IGNORE INTO pipeline_runs (run_id, stage, started_at, status) "
        "VALUES (?, 'test', '2026-01-01T00:00:00Z', 'success')",
        (run_id,),
    )
    return run_id


def make_security(conn, symbol, cik):
    security_id = identity.create_security(
        conn, name=f"{symbol} Inc.", cik=cik, security_type="common_stock",
        classification_confidence="high", classification_source="test",
        first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )
    identity.add_listing(conn, security_id=security_id, symbol=symbol, exchange="NYSE",
                          valid_from="2020-01-01")
    return security_id


# --------------------------------------------------------- 1. progress bookkeeping


def test_pending_items_excludes_success_and_skipped_but_not_failed(conn):
    fake_run(conn, "run-1")
    P.mark_item(conn, "b1", "prices", "AAPL", "success", "run-1")
    P.mark_item(conn, "b1", "prices", "MSFT", "failed", "run-1", "boom")
    P.mark_item(conn, "b1", "prices", "GOOG", "skipped", "run-1")

    pending = P.pending_items(conn, "b1", "prices", ["AAPL", "MSFT", "GOOG", "AMZN"])

    assert pending == ["MSFT", "AMZN"], "only failed and never-attempted items are pending"


def test_pending_items_is_scoped_per_batch_id(conn):
    fake_run(conn, "run-1")
    P.mark_item(conn, "batch-a", "prices", "AAPL", "success", "run-1")
    pending_same_batch = P.pending_items(conn, "batch-a", "prices", ["AAPL"])
    pending_other_batch = P.pending_items(conn, "batch-b", "prices", ["AAPL"])

    assert pending_same_batch == []
    assert pending_other_batch == ["AAPL"], "a different batch_id must redo its own work"


def test_retrying_a_failed_item_overwrites_its_row_not_duplicates_it(conn):
    fake_run(conn, "run-1")
    fake_run(conn, "run-2")
    P.mark_item(conn, "b1", "xbrl", "0000320193", "failed", "run-1", "timeout")
    P.mark_item(conn, "b1", "xbrl", "0000320193", "success", "run-2")

    rows = conn.execute(
        "SELECT status, run_id FROM orchestration_progress "
        "WHERE batch_id = 'b1' AND stage = 'xbrl' AND item_key = '0000320193'"
    ).fetchall()
    assert len(rows) == 1, "a retry replaces the row for that item, never adds a second one"
    assert rows[0]["status"] == "success"
    assert rows[0]["run_id"] == "run-2"


# ------------------------------------------ 2. interrupted-run resume, no loss/dup


def test_a_failure_partway_through_is_retried_on_resume_without_redoing_successes(
    conn, monkeypatch
):
    securities = [make_security(conn, f"SYM{i}", f"000000{i:04d}") for i in range(5)]
    symbols = [f"SYM{i}" for i in range(5)]
    calls: list[str] = []

    class FakeReport:
        rows_inserted = 1
        revisions_detected = 0

    failed_once = {"done": False}

    def fake_ingest_securities(conn, provider, securities, years, run_id, verbose=True):
        (sid, sym) = securities[0]
        calls.append(sym)
        if sym == "SYM2" and not failed_once["done"]:
            failed_once["done"] = True
            raise RuntimeError("simulated network failure")
        return FakeReport()

    monkeypatch.setattr(R, "ingest_securities", fake_ingest_securities)
    monkeypatch.setattr(R, "get_provider", lambda: None)
    monkeypatch.setattr(
        R, "_security_list",
        lambda conn, tier, pool, limit: [(sid, None, sym) for sid, sym in zip(securities, symbols)],
    )

    first = R.run_prices_tier(conn, "resume-test", None, 3, None)
    assert calls == ["SYM0", "SYM1", "SYM2", "SYM3", "SYM4"], (
        "a single failure must not abort the rest of the batch"
    )
    assert first["summary"] == {"success": 4, "failed": 1, "skipped": 0}

    calls.clear()
    second = R.run_prices_tier(conn, "resume-test", None, 3, None)
    assert calls == ["SYM2"], "resume must retry only the failed item, not redo the four successes"
    assert second["summary"] == {"success": 5, "failed": 0, "skipped": 0}


def test_a_partial_run_never_overwrites_a_complete_ones_pipeline_runs_row(conn, monkeypatch):
    securities = [make_security(conn, f"SYM{i}", f"000000{i:04d}") for i in range(2)]
    symbols = [f"SYM{i}" for i in range(2)]

    class FakeReport:
        rows_inserted = 1
        revisions_detected = 0

    monkeypatch.setattr(
        R, "ingest_securities",
        lambda conn, provider, securities, years, run_id, verbose=True: FakeReport(),
    )
    monkeypatch.setattr(R, "get_provider", lambda: None)
    monkeypatch.setattr(
        R, "_security_list",
        lambda conn, tier, pool, limit: [(sid, None, sym) for sid, sym in zip(securities, symbols)],
    )

    first = R.run_prices_tier(conn, "complete-batch", None, 3, None)
    first_row = dict(
        conn.execute(
            "SELECT status, finished_at FROM pipeline_runs WHERE run_id = ?", (first["run_id"],)
        ).fetchone()
    )
    assert first_row["status"] == "success"

    # Re-running the same, now fully-done batch_id must not touch the first run's row.
    second = R.run_prices_tier(conn, "complete-batch", None, 3, None)
    assert second["run_id"] != first["run_id"], "a resume attempt always gets its own run_id"

    first_row_after = dict(
        conn.execute(
            "SELECT status, finished_at FROM pipeline_runs WHERE run_id = ?", (first["run_id"],)
        ).fetchone()
    )
    assert first_row_after == first_row, "the original complete run's row must be untouched"


# ----------------------------------------------------- 3. consecutive-failure breaker


def test_consecutive_failures_abort_the_batch_early(conn, monkeypatch):
    securities = [make_security(conn, f"SYM{i}", f"000000{i:04d}") for i in range(10)]
    symbols = [f"SYM{i}" for i in range(10)]
    calls: list[str] = []

    def always_fails(conn, provider, securities, years, run_id, verbose=True):
        (sid, sym) = securities[0]
        calls.append(sym)
        raise RuntimeError("simulated systemic outage")

    monkeypatch.setattr(R, "ingest_securities", always_fails)
    monkeypatch.setattr(R, "get_provider", lambda: None)
    monkeypatch.setattr(
        R, "_security_list",
        lambda conn, tier, pool, limit: [(sid, None, sym) for sid, sym in zip(securities, symbols)],
    )

    result = R.run_prices_tier(conn, "breaker-test", None, 3, None)

    assert len(calls) == R.MAX_CONSECUTIVE_FAILURES, (
        "must stop after the configured number of consecutive failures, not grind through all 10"
    )
    assert any("aborting" in message for message in result["errors"])


def test_a_success_between_failures_resets_the_consecutive_counter(conn, monkeypatch):
    securities = [make_security(conn, f"SYM{i}", f"000000{i:04d}") for i in range(12)]
    symbols = [f"SYM{i}" for i in range(12)]

    class FakeReport:
        rows_inserted = 1
        revisions_detected = 0

    # Fails, fails, succeeds, fails, fails, succeeds... never MAX_CONSECUTIVE_FAILURES in a row.
    def alternating(conn, provider, securities, years, run_id, verbose=True):
        (sid, sym) = securities[0]
        idx = int(sym.replace("SYM", ""))
        if idx % 3 == 2:
            return FakeReport()
        raise RuntimeError("transient")

    monkeypatch.setattr(R, "ingest_securities", alternating)
    monkeypatch.setattr(R, "get_provider", lambda: None)
    monkeypatch.setattr(
        R, "_security_list",
        lambda conn, tier, pool, limit: [(sid, None, sym) for sid, sym in zip(securities, symbols)],
    )

    result = R.run_prices_tier(conn, "no-break-test", None, 3, None)

    assert result["summary"]["success"] == 4  # indices 2, 5, 8, 11
    assert result["summary"]["failed"] == 8
    assert not any("aborting" in message for message in result["errors"]), (
        "no run of failures ever reached the threshold, so it must not abort early"
    )


# --------------------------------------------------------------- 4. rate limiting


def test_rate_limiter_enforces_minimum_spacing():
    from universe.sec_client import RateLimiter

    limiter = RateLimiter(rate_per_second=10.0)  # matches SEC's own guidance
    started = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    elapsed = time.monotonic() - started

    # 5 calls at 10/s must take at least 0.4s (4 gaps of >= 0.1s each).
    assert elapsed >= 0.35, f"rate limiter allowed {5 / elapsed:.1f} calls/s, expected <= 10/s"


def test_rate_limiter_rejects_a_rate_above_the_sec_ceiling():
    from universe.sec_client import SecClientError

    with pytest.raises(SecClientError):
        from universe.sec_client import SecClient

        SecClient(user_agent="Test test@example.com", rate_per_second=11.0)
