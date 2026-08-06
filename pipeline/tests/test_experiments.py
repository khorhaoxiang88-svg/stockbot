"""experiments (migration 022): the official forward experiment, and the
query-layer views that permanently exclude pre-launch trades from official
statistics.
"""

from __future__ import annotations

import sqlite3

import pytest

import migrate
from launch.open_experiment import LaunchError, open_experiment
from scoring.compute import config_hash


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "experiments.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def seed_security(conn, security_id=1, symbol="AAAA"):
    conn.execute(
        "INSERT INTO securities (security_id, cik, share_class, name, security_type, "
        "classification_confidence, classification_source, sic_code, first_seen, "
        "last_seen, is_active, delisted_date) VALUES (?, ?, NULL, ?, 'common_stock', "
        "'high', 'test', '3571', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, NULL)",
        (security_id, f"{security_id:010d}", f"{symbol} Inc."),
    )
    conn.execute(
        "INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, "
        "is_primary) VALUES (?, ?, 'NYSE', '2026-01-01', NULL, 1)",
        (security_id, symbol),
    )


def seed_snapshot_and_run(conn):
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


def seed_candidate(conn, candidate_id, security_id, generated_at, code_version="selection-rule-1.1/v1"):
    conn.execute(
        "INSERT INTO research_candidates (candidate_id, security_id, generated_at, "
        "data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash, "
        "code_version, selection_rule_version, mapping_version, price_dataset_version, "
        "price_snapshot_hash, source_health_snapshot_json, score_snapshot_json, "
        "accessions_used_json, composite_at_generation, rank_at_generation, "
        "signal_close, atr_value, atr_window, price_data_cutoff, entry_rule, "
        "gap_limit_atr, row_hash) VALUES (?, ?, ?, ?, 'snap-1', 'run-seed', 1, 'h', "
        "?, 1, '1', 1, 'psh', '{}', '{}', '[]', 55.0, 1, 100.0, 3.0, 14, ?, "
        "'next_open', 1.0, ?)",
        (candidate_id, security_id, generated_at, generated_at, code_version,
         generated_at[:10], f"rh-{candidate_id}"),
    )


def seed_position(conn, position_id, candidate_id, status="closed", exit_price=110.0):
    net_pnl = (exit_price - 100.0) * 10 if status == "closed" else None
    conn.execute(
        "INSERT INTO paper_positions (position_id, candidate_id, horizon_days, book_id, "
        "protocol_version, strategy_version, resolution_policy_version, "
        "accrual_policy_version, price_snapshot_hash, opened_run_id, last_evaluated_at, "
        "entry_date, entry_price, slippage_bps, shares, notional, stop_price, "
        "target_price, status, exit_date, exit_price, exit_reason, gross_pnl, net_pnl, "
        "pnl_pct) VALUES (?, ?, 20, 'book-20d', 'v1', 1, 1, 1, 'h', 'run-seed', 'x', "
        "'2026-07-01', 100.0, 5, 10, 1000, 90.0, 110.0, ?, ?, ?, ?, ?, ?, ?)",
        (position_id, candidate_id, status,
         "2026-07-10" if status == "closed" else None,
         exit_price if status == "closed" else None,
         "target" if status == "closed" else None,
         net_pnl, net_pnl, net_pnl),
    )


def seed_experiment(conn, started_at="2026-08-01T00:00:00Z", experiment_id="exp-1"):
    conn.execute(
        "INSERT INTO experiments (experiment_id, strategy_version, "
        "selection_rule_version, protocol_version, config_hash, started_at, status) "
        "VALUES (?, 2, 2, 1, 'h', ?, 'active')",
        (experiment_id, started_at),
    )


# --------------------------------- 1. no pre-launch row in an official statistic


def test_a_candidate_generated_before_the_experiment_never_appears_official(conn):
    seed_security(conn)
    seed_snapshot_and_run(conn)
    seed_experiment(conn, started_at="2026-08-01T00:00:00Z")
    seed_candidate(conn, "cand-pre", 1, generated_at="2026-07-31T23:59:59Z")

    official = conn.execute("SELECT candidate_id FROM official_candidates").fetchall()
    assert official == []


def test_a_candidate_generated_at_or_after_the_experiment_is_official(conn):
    seed_security(conn)
    seed_snapshot_and_run(conn)
    seed_experiment(conn, started_at="2026-08-01T00:00:00Z")
    seed_candidate(conn, "cand-post", 1, generated_at="2026-08-01T00:00:00Z")

    official = [r["candidate_id"] for r in conn.execute("SELECT candidate_id FROM official_candidates")]
    assert official == ["cand-post"]


def test_a_provisional_or_pool_scoped_candidate_never_counts_as_official_even_if_recent(conn):
    """Regression shape: a code_version suffix, not just the date, gates
    official status -- a --pool or --provisional-threshold run after launch
    must still never count."""
    seed_security(conn)
    seed_snapshot_and_run(conn)
    seed_experiment(conn, started_at="2026-08-01T00:00:00Z")
    seed_candidate(conn, "cand-provisional", 1, generated_at="2026-08-05T00:00:00Z",
                    code_version="selection-rule-1.1/v1+provisional")
    seed_candidate(conn, "cand-pool", 1, generated_at="2026-08-05T00:00:00Z",
                    code_version="selection-rule-1.1/v1+pool[s1-sample-v1]")

    official = conn.execute("SELECT candidate_id FROM official_candidates").fetchall()
    assert official == []


def test_no_pre_launch_position_appears_official_under_aggregation_either(conn):
    """'Under any query' -- an aggregate over official_positions must not be
    inflated by a pre-launch row, the same as a plain SELECT isn't."""
    seed_security(conn)
    seed_snapshot_and_run(conn)
    seed_experiment(conn, started_at="2026-08-01T00:00:00Z")
    seed_candidate(conn, "cand-pre", 1, generated_at="2026-07-01T00:00:00Z")
    seed_candidate(conn, "cand-post", 1, generated_at="2026-08-02T00:00:00Z")
    seed_position(conn, "pos-pre", "cand-pre", exit_price=999.0)
    seed_position(conn, "pos-post", "cand-post", exit_price=120.0)

    count = conn.execute("SELECT COUNT(*) AS n FROM official_positions").fetchone()["n"]
    assert count == 1
    total_pnl = conn.execute(
        "SELECT SUM(net_pnl) AS s FROM official_positions"
    ).fetchone()["s"]
    assert total_pnl == pytest.approx((120.0 - 100.0) * 10)


def test_no_active_experiment_means_official_views_are_always_empty(conn):
    seed_security(conn)
    seed_snapshot_and_run(conn)
    seed_candidate(conn, "cand-1", 1, generated_at="2026-08-05T00:00:00Z")
    seed_position(conn, "pos-1", "cand-1")
    assert conn.execute("SELECT COUNT(*) AS n FROM official_candidates").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM official_positions").fetchone()["n"] == 0


# ------------------------------------------------------- 2. experiment immutability


def test_core_experiment_fields_are_immutable(conn):
    seed_experiment(conn)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE experiments SET strategy_version = 99 WHERE experiment_id = 'exp-1'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE experiments SET started_at = '2000-01-01T00:00:00Z' WHERE experiment_id = 'exp-1'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE experiments SET config_hash = 'tampered' WHERE experiment_id = 'exp-1'")


def test_status_may_transition_from_active_to_ended(conn):
    seed_experiment(conn)
    conn.execute(
        "UPDATE experiments SET status = 'ended', ended_at = '2026-09-01T00:00:00Z' "
        "WHERE experiment_id = 'exp-1'"
    )
    row = conn.execute("SELECT status, ended_at FROM experiments WHERE experiment_id = 'exp-1'").fetchone()
    assert row["status"] == "ended"
    assert row["ended_at"] == "2026-09-01T00:00:00Z"


def test_experiments_are_never_deleted(conn):
    seed_experiment(conn)
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM experiments WHERE experiment_id = 'exp-1'")


def test_at_most_one_active_experiment_at_a_time(conn):
    seed_experiment(conn, experiment_id="exp-1")
    with pytest.raises(sqlite3.IntegrityError):
        seed_experiment(conn, experiment_id="exp-2")


def test_an_ended_experiment_does_not_block_a_new_active_one(conn):
    seed_experiment(conn, experiment_id="exp-1")
    conn.execute(
        "UPDATE experiments SET status = 'ended', ended_at = '2026-09-01T00:00:00Z' "
        "WHERE experiment_id = 'exp-1'"
    )
    seed_experiment(conn, experiment_id="exp-2", started_at="2026-09-02T00:00:00Z")
    active = conn.execute("SELECT experiment_id FROM experiments WHERE status = 'active'").fetchone()
    assert active["experiment_id"] == "exp-2"


# --------------------------------------------------- 3. launch respects the config lock


def test_open_experiment_refuses_when_no_lock_exists(conn):
    cfg = {"strategy_version": 2, "selection_rule_version": 2, "protocol_version": 1}
    with pytest.raises(SystemExit, match="no frozen_config_lock row"):
        open_experiment(conn, cfg, running_hash="anything", pool_versions=[], as_of_date="2026-08-06")


def test_open_experiment_refuses_on_a_hash_mismatch(conn):
    conn.execute(
        "INSERT INTO calibration_reports (report_id, computed_at, score_date, "
        "config_hash, report_json) VALUES ('calib-1', '2026-08-06T00:00:00Z', "
        "'2026-08-03', 'x', '{}')"
    )
    conn.execute(
        "INSERT INTO frozen_config_lock (strategy_version, selection_rule_version, "
        "config_hash, calibration_report_id, locked_at) VALUES "
        "(2, 2, 'locked-hash', 'calib-1', '2026-08-06T00:00:00Z')"
    )
    cfg = {"strategy_version": 2, "selection_rule_version": 2, "protocol_version": 1}
    with pytest.raises(SystemExit, match="REFUSING to generate official candidates"):
        open_experiment(conn, cfg, running_hash="drifted-hash", pool_versions=[], as_of_date="2026-08-06")


def test_open_experiment_refuses_when_one_is_already_active(conn):
    seed_experiment(conn)
    cfg = {"strategy_version": 2, "selection_rule_version": 2, "protocol_version": 1}
    with pytest.raises(LaunchError, match="already active"):
        open_experiment(conn, cfg, running_hash="whatever", pool_versions=[], as_of_date="2026-08-06")


# ------------------------------------------------------------ against the real lock


def test_the_real_experiment_if_any_matches_the_current_frozen_config_hash():
    """If an experiment has actually been opened for real, its config_hash
    must still be the hash config.frozen.json produces today -- otherwise the
    launched experiment and the current file have silently diverged."""
    import migrate as real_migrate

    conn = real_migrate.connect()
    try:
        row = conn.execute(
            "SELECT config_hash FROM experiments WHERE status = 'active'"
        ).fetchone()
        if row is None:
            pytest.skip("no experiment opened yet")
        assert row["config_hash"] == config_hash()
    finally:
        conn.close()
