"""O3, migration 023: defect_log (the bug-correction policy's audit trail)
and the paper_positions/benchmark_positions closed-immutability guards.

Both are schema-level enforcement, not convention -- every test here proves
a raw SQL statement is refused by SQLite itself, independent of any Python
code path that would or wouldn't attempt it.
"""

from __future__ import annotations

import sqlite3

import pytest

import migrate


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "defect_log.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def seed_lock(conn, strategy_version):
    conn.execute(
        "INSERT INTO calibration_reports (report_id, computed_at, score_date, "
        "config_hash, report_json) VALUES (?, 'x', '2026-01-01', 'h', '{}')",
        (f"calib-{strategy_version}",),
    )
    conn.execute(
        "INSERT INTO frozen_config_lock (strategy_version, selection_rule_version, "
        "config_hash, calibration_report_id, locked_at) VALUES (?, 1, 'h', ?, 'x')",
        (strategy_version, f"calib-{strategy_version}"),
    )


def insert_defect(conn, defect_id, severity, affected=None, new_version=None, published=None):
    conn.execute(
        "INSERT INTO defect_log (defect_id, discovered_at, severity, description, "
        "affected_strategy_version, new_strategy_version, published_at) "
        "VALUES (?, 'x', ?, 'd', ?, ?, ?)",
        (defect_id, severity, affected, new_version, published),
    )


# ------------------------------------------------------------- defect_log shape


def test_severity_is_restricted_to_the_three_policy_tiers(conn):
    with pytest.raises(sqlite3.IntegrityError):
        insert_defect(conn, "d1", "catastrophic")


def test_a_material_defect_must_name_the_affected_strategy_version(conn):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_defect(conn, "d1", "material", affected=None)


def test_a_cosmetic_defect_needs_no_affected_strategy_version(conn):
    insert_defect(conn, "d1", "cosmetic", affected=None)  # must not raise
    conn.commit()


def test_only_a_material_defect_may_carry_a_new_strategy_version(conn):
    seed_lock(conn, 2)
    seed_lock(conn, 3)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_defect(conn, "d1", "cosmetic", affected=None, new_version=3)


def test_the_new_strategy_version_must_differ_from_the_affected_one(conn):
    seed_lock(conn, 2)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_defect(conn, "d1", "material", affected=2, new_version=2)


def test_a_material_defect_recording_its_new_strategy_version_is_valid(conn):
    seed_lock(conn, 2)
    seed_lock(conn, 3)
    insert_defect(conn, "d1", "material", affected=2, new_version=3)  # must not raise
    conn.commit()


# ------------------------------------------------------------- defect_log immutability


def test_defect_log_core_fields_are_immutable(conn):
    insert_defect(conn, "d1", "cosmetic")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE defect_log SET description = 'rewritten' WHERE defect_id = 'd1'")


def test_defect_log_resolution_and_published_at_may_be_filled_in_later(conn):
    insert_defect(conn, "d1", "cosmetic")
    conn.commit()
    conn.execute(
        "UPDATE defect_log SET resolution = 'fixed', published_at = 'y' WHERE defect_id = 'd1'"
    )
    conn.commit()  # must not raise
    row = conn.execute("SELECT resolution, published_at FROM defect_log WHERE defect_id = 'd1'").fetchone()
    assert row["resolution"] == "fixed"
    assert row["published_at"] == "y"


def test_defect_log_rows_are_never_deleted(conn):
    insert_defect(conn, "d1", "cosmetic")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM defect_log WHERE defect_id = 'd1'")


# ------------------------------------------------- result immutability (positions)


def seed_position_and_candidate(conn, position_id, status="open"):
    conn.execute(
        "INSERT INTO securities (security_id, cik, share_class, name, security_type, "
        "classification_confidence, classification_source, sic_code, first_seen, "
        "last_seen, is_active, delisted_date) VALUES (1, '0000000001', NULL, 'Acme', "
        "'common_stock', 'high', 'test', '3571', 'x', 'x', 1, NULL)"
    )
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES ('run-seed', 'test', 'x', 'success', 'x')"
    )
    conn.execute(
        "INSERT INTO universe_snapshot_runs (snapshot_id, effective_at, rules_version, "
        "config_hash, run_id, security_count, is_official) VALUES "
        "('snap-1', '2026-01-01', 'v', 'h', NULL, 1, 1)"
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
        "gap_limit_atr, row_hash) VALUES ('cand-a', 1, 'x', 'x', 'snap-1', 'run-seed', "
        "1, 'h', 'v', 1, '1', 1, 'psh', '{}', '{}', '[]', 55.0, 1, 100.0, 3.0, 14, "
        "'2026-01-01', 'next_open', 1.0, 'rh')"
    )
    if status == "closed":
        conn.execute(
            "INSERT INTO paper_positions (position_id, candidate_id, horizon_days, "
            "book_id, protocol_version, strategy_version, resolution_policy_version, "
            "accrual_policy_version, opened_run_id, last_evaluated_at, entry_date, "
            "entry_price, slippage_bps, shares, notional, stop_price, target_price, "
            "status, exit_date, exit_price, exit_reason, gross_pnl, net_pnl, pnl_pct) "
            "VALUES (?, 'cand-a', 20, 'book-20d', 'v1', 1, 1, 1, 'run-seed', 'x', "
            "'2026-01-01', 100.0, 5, 10, 1000, 90.0, 110.0, 'closed', '2026-01-10', "
            "112.0, 'target', 120, 120, 0.12)",
            (position_id,),
        )
    else:
        conn.execute(
            "INSERT INTO paper_positions (position_id, candidate_id, horizon_days, "
            "book_id, protocol_version, strategy_version, resolution_policy_version, "
            "accrual_policy_version, opened_run_id, last_evaluated_at, entry_date, "
            "entry_price, slippage_bps, shares, notional, stop_price, target_price, "
            "status) VALUES (?, 'cand-a', 20, 'book-20d', 'v1', 1, 1, 1, 'run-seed', "
            "'x', '2026-01-01', 100.0, 5, 10, 1000, 90.0, 110.0, 'open')",
            (position_id,),
        )


def test_a_closed_paper_position_may_never_be_updated(conn):
    seed_position_and_candidate(conn, "pos-a", status="closed")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE paper_positions SET net_pnl = 999 WHERE position_id = 'pos-a'")


def test_an_open_paper_position_may_still_be_updated_normally(conn):
    seed_position_and_candidate(conn, "pos-a", status="open")
    conn.commit()
    conn.execute(
        "UPDATE paper_positions SET last_evaluated_at = 'y' WHERE position_id = 'pos-a'"
    )
    conn.commit()  # must not raise -- daily monitoring still works


def test_a_paper_position_may_transition_into_closed_exactly_once(conn):
    seed_position_and_candidate(conn, "pos-a", status="open")
    conn.commit()
    conn.execute(
        "UPDATE paper_positions SET status = 'closed', exit_date = 'y', exit_price = 1.0, "
        "exit_reason = 'target', net_pnl = 1 WHERE position_id = 'pos-a'"
    )
    conn.commit()  # the first close must not raise
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE paper_positions SET net_pnl = 999 WHERE position_id = 'pos-a'")


def test_paper_positions_are_never_deleted(conn):
    seed_position_and_candidate(conn, "pos-a", status="open")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM paper_positions WHERE position_id = 'pos-a'")


def test_a_closed_benchmark_position_may_never_be_updated(conn):
    seed_position_and_candidate(conn, "pos-a", status="open")
    conn.execute(
        "INSERT INTO benchmark_positions (position_id, candidate_id, horizon_days, "
        "book_id, security_id, protocol_version, strategy_version, "
        "resolution_policy_version, accrual_policy_version, opened_run_id, "
        "last_evaluated_at, entry_date, entry_price, slippage_bps, shares, notional, "
        "status, exit_date, exit_price, exit_reason, gross_pnl, net_pnl, pnl_pct) "
        "VALUES ('bench-a', 'cand-a', 20, 'book-20d', 1, 'v1', 1, 1, 1, 'run-seed', "
        "'x', '2026-01-01', 100.0, 5, 10, 1000, 'closed', '2026-01-10', 112.0, "
        "'matched_close', 120, 120, 0.12)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE benchmark_positions SET net_pnl = 999 WHERE position_id = 'bench-a'")


def test_benchmark_positions_are_never_deleted(conn):
    seed_position_and_candidate(conn, "pos-a", status="open")
    conn.execute(
        "INSERT INTO benchmark_positions (position_id, candidate_id, horizon_days, "
        "book_id, security_id, protocol_version, strategy_version, "
        "resolution_policy_version, accrual_policy_version, opened_run_id, "
        "last_evaluated_at, entry_date, entry_price, slippage_bps, shares, notional, "
        "status) VALUES ('bench-a', 'cand-a', 20, 'book-20d', 1, 'v1', 1, 1, 1, "
        "'run-seed', 'x', '2026-01-01', 100.0, 5, 10, 1000, 'open')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM benchmark_positions WHERE position_id = 'bench-a'")
