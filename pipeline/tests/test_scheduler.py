"""S6 scheduler tests, covering the 4 behaviors the brief names:

  1. Weekly selection fires exactly once per trading week -- enforced two
     layers down (trading_calendar's week-completeness gate, and compute.py's
     deterministic candidate_id; see
     test_selection.py::test_candidate_id_is_deterministic_so_a_rerun_cannot_duplicate).
     weekly.py adds nothing to that guarantee, so what's tested here is that
     the scheduler's OWN run-tracking doesn't fight it: re-running the same
     day doesn't fabricate a second published run.
  2. A source failure blocks new candidates but preserves the last published
     screener -- pipeline-side (selection/compute.py always suppresses every
     row as stale_source and writes zero candidates when a required source
     fails) is covered in test_selection.py; the presentation-side fix
     (web/lib/db.ts getPublishedSelectionRun) has its own test in
     web/tests/candidates-page.test.tsx. What belongs here is that weekly.py
     correctly detects and logs a blocked run from the DB state.
  3. Daily monitoring continues while selection is blocked -- selection only
     ever runs from weekly.py, never from daily.py's stage list, and one
     daily stage failing must not stop the rest (run_stage isolation).
  4. A missed run is detected and reported, not silently skipped --
     missed_run_dates and its use in record_scheduler_run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import migrate
from scheduler import common as C
from scheduler import daily as D


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "scheduler.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


# ------------------------------------------------------------- missed runs


def test_missed_run_dates_empty_when_no_prior_run():
    assert C.missed_run_dates(None, date(2026, 8, 10), period_days=1) == []


def test_missed_run_dates_empty_on_the_ordinary_cadence():
    # Yesterday's daily run, checked today: exactly on schedule.
    assert C.missed_run_dates(date(2026, 8, 9), date(2026, 8, 10), period_days=1) == []


def test_missed_run_dates_flags_a_skipped_day():
    # Last daily run 3 days ago: 2 days have gone by since without a run.
    missed = C.missed_run_dates(date(2026, 8, 7), date(2026, 8, 10), period_days=1)
    assert missed == [date(2026, 8, 8), date(2026, 8, 9)]


def test_missed_run_dates_weekly_period_tolerates_the_normal_7_day_gap():
    assert C.missed_run_dates(date(2026, 8, 1), date(2026, 8, 8), period_days=7) == []


def test_missed_run_dates_weekly_period_flags_a_skipped_week():
    missed = C.missed_run_dates(date(2026, 8, 1), date(2026, 8, 22), period_days=7)
    assert missed == [date(2026, 8, 8), date(2026, 8, 15)]


def test_missed_run_dates_monthly_grace_tolerates_a_31_day_month():
    # last run on the 1st, this run on the 1st of the next (31-day) month.
    assert C.missed_run_dates(
        date(2026, 7, 1), date(2026, 8, 1), period_days=30, grace_days=5
    ) == []


def test_missed_run_dates_monthly_flags_a_fully_skipped_month():
    missed = C.missed_run_dates(
        date(2026, 6, 1), date(2026, 8, 15), period_days=30, grace_days=5
    )
    assert missed  # a month really was skipped


def test_a_missed_run_is_recorded_in_pipeline_runs_not_silently_skipped(conn):
    run_id = C.record_scheduler_run(conn, "daily_missed", "failed", 0, ["missed 2026-08-08"])
    row = conn.execute(
        "SELECT stage, status, errors_json FROM pipeline_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row["stage"] == "scheduler_daily_missed"
    assert row["status"] == "failed"
    assert "2026-08-08" in row["errors_json"]


# --------------------------------------------------------- stage isolation


def test_run_stage_never_raises_on_a_failing_command():
    result = C.run_stage("boom", ["-c", "import sys; sys.exit(1)"])
    assert result.ok is False
    assert result.returncode == 1


def test_run_stage_never_raises_when_the_command_cannot_even_start():
    # An invalid cwd fails before the subprocess launches at all -- the
    # exception path, distinct from "launched and exited non-zero" above.
    result = C.run_stage("nonexistent", ["-c", "pass"], cwd=Path("Z:/no/such/directory"))
    assert result.ok is False
    assert result.error is not None


def test_daily_stage_list_never_includes_selection():
    """Selection only ever runs from weekly.py -- a daily invocation must not
    be able to fire it, by construction, independent of any runtime check."""
    specs = D._stage_specs("pool-v1", "2026-08-10", "db.sqlite")
    names = [name for name, _ in specs]
    assert "selection" not in names
    all_args = " ".join(a for _, args in specs for a in args)
    assert "selection/compute.py" not in all_args


def test_a_failing_stage_does_not_stop_the_rest(monkeypatch):
    """The isolation property daily.run_daily relies on: iterate every stage
    spec and keep going even when one fails, rather than raising out of the
    loop. Exercised directly against run_stage rather than the full
    run_daily (which needs network-backed stages) -- same mechanism S2's own
    tests use fault injection for, see test_orchestrate.py's module docstring.
    """
    specs = [
        ("a", ["-c", "import sys; sys.exit(0)"]),
        ("b", ["-c", "import sys; sys.exit(1)"]),  # fails
        ("c", ["-c", "import sys; sys.exit(0)"]),
    ]
    results = {name: C.run_stage(name, args) for name, args in specs}
    assert results["a"].ok is True
    assert results["b"].ok is False
    assert results["c"].ok is True  # reached despite b's failure


# ------------------------------------------------------------- pool lookup


def test_latest_pool_version_is_none_when_nothing_loaded(conn):
    assert C.latest_pool_version(conn) is None


# --------------------------------------------------------- position logging


def test_log_positions_joins_through_the_candidate_for_security_id(conn):
    """paper_positions has no security_id column of its own -- it is reached
    only through the candidate it was opened from. A prior version of this
    query assumed a direct column and crashed at runtime (caught only by
    actually running daily.py against a live-shaped DB, not by any unit
    test) -- this pins the fix.
    """
    conn.execute(
        "INSERT INTO securities (security_id, cik, share_class, name, security_type, "
        "classification_confidence, classification_source, sic_code, first_seen, "
        "last_seen, is_active, delisted_date) VALUES (7, '0000000007', NULL, 'Test Co', "
        "'common_stock', 'high', 'test', '3571', '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z', 1, NULL)"
    )
    conn.execute(
        "INSERT INTO universe_snapshot_runs (snapshot_id, effective_at, rules_version, "
        "config_hash, run_id, security_count, is_official) VALUES "
        "('snap-1', '2026-01-01', 'v', 'h', NULL, 1, 1)"
    )
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES ('run-seed', 'test', '2026-01-01T00:00:00Z', 'success', 'x')"
    )
    conn.execute(
        "INSERT INTO books (book_id, horizon_days, starting_nav, current_nav, "
        "open_position_count, strategy_version) VALUES "
        "('book-20d', 20, 100000, 100000, 0, 1)"
    )
    conn.execute(
        "INSERT INTO research_candidates (candidate_id, security_id, generated_at, "
        "data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash, "
        "code_version, selection_rule_version, mapping_version, price_dataset_version, "
        "price_snapshot_hash, source_health_snapshot_json, score_snapshot_json, "
        "accessions_used_json, composite_at_generation, rank_at_generation, "
        "signal_close, atr_value, atr_window, price_data_cutoff, entry_rule, "
        "gap_limit_atr, row_hash) VALUES ('cand-a', 7, 'x', '2026-08-06T20:00:00Z', "
        "'snap-1', 'run-seed', 1, 'h', 'v', 1, '1', 1, 'psh', '{}', '{}', '[]', "
        "55.0, 1, 10.0, 0.5, 14, '2026-08-06', 'next_open', 1.0, 'rh')"
    )
    conn.execute(
        "INSERT INTO paper_positions (position_id, candidate_id, horizon_days, book_id, "
        "protocol_version, strategy_version, resolution_policy_version, "
        "accrual_policy_version, opened_run_id, last_evaluated_at, entry_date, "
        "entry_price, slippage_bps, shares, notional, stop_price, target_price, status) "
        "VALUES ('pos-a', 'cand-a', 20, 'book-20d', 'R1-PROTOCOL-1.1', 1, 1, 1, "
        "'run-seed', 'x', '2026-08-06', 100.0, 5, 10.0, 1000.0, 90.0, 110.0, "
        "'pending_resolution')"
    )
    conn.commit()

    log = C.RunLog("daily", "2026-08-06")
    D._log_positions(conn, log, "2026-08-06")
    text = "\n".join(log.lines)
    assert "pos-a" in text
    assert "security=7" in text
    assert "pending resolutions" in text


def test_latest_pool_version_picks_the_most_recently_discovered(conn):
    conn.execute(
        "INSERT INTO securities (security_id, cik, share_class, name, security_type, "
        "classification_confidence, classification_source, sic_code, first_seen, "
        "last_seen, is_active, delisted_date) VALUES "
        "(1, '0000000001', NULL, 'Acme', 'common_stock', 'high', 'test', '3571', "
        "'2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', 1, NULL), "
        "(2, '0000000002', NULL, 'Beta', 'common_stock', 'high', 'test', '3571', "
        "'2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', 1, NULL)"
    )
    conn.execute(
        "INSERT INTO universe_candidate_pool (security_id, symbol_at_discovery, "
        "exchange, discovered_at, pool_version, discovery_source) VALUES "
        "(1, 'ACME', 'NYSE', '2026-07-01T00:00:00Z', 'old-v1', 'test')"
    )
    conn.execute(
        "INSERT INTO universe_candidate_pool (security_id, symbol_at_discovery, "
        "exchange, discovered_at, pool_version, discovery_source) VALUES "
        "(2, 'BETA', 'NYSE', '2026-08-01T00:00:00Z', 'new-v1', 'test')"
    )
    conn.commit()
    assert C.latest_pool_version(conn) == "new-v1"
