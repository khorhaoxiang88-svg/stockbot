"""frozen_config_lock (migration 021): the startup check that refuses to
generate an official candidate when the running config has drifted from what
was actually locked in and calibrated against.
"""

from __future__ import annotations

import json

import pytest

import migrate
from scoring.compute import config_hash
from selection.compute import verify_frozen_config_lock

CFG = {"strategy_version": 2}


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "lock.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def seed_calibration_report(conn, report_id="calib-test"):
    conn.execute(
        "INSERT INTO calibration_reports (report_id, computed_at, score_date, "
        "config_hash, report_json) VALUES (?, '2026-08-06T00:00:00Z', "
        "'2026-08-03', 'x', '{}')",
        (report_id,),
    )


def seed_lock(conn, strategy_version, config_hash_value, report_id="calib-test"):
    conn.execute(
        "INSERT INTO frozen_config_lock (strategy_version, selection_rule_version, "
        "config_hash, calibration_report_id, locked_at) VALUES (?, 2, ?, ?, "
        "'2026-08-06T00:00:00Z')",
        (strategy_version, config_hash_value, report_id),
    )


# --------------------------------------- 1. altering a parameter changes the hash


def test_altering_any_frozen_parameter_changes_the_hash(tmp_path):
    original = {"strategy_version": 2, "composite_threshold": 55, "atr_window": 14}
    path = tmp_path / "config.frozen.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    before = config_hash(path)

    altered = dict(original)
    altered["atr_window"] = 21  # not even a governed key -- still must change the hash
    path.write_text(json.dumps(altered), encoding="utf-8")
    after = config_hash(path)

    assert before != after


def test_an_unrelated_edit_like_whitespace_also_changes_the_hash(tmp_path):
    """The hash is over raw bytes, deliberately -- it pins the whole file, not
    a semantic subset the way governed_digest does."""
    path = tmp_path / "config.frozen.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    before = config_hash(path)
    path.write_text('{"a": 1}\n', encoding="utf-8")
    after = config_hash(path)
    assert before != after


# ------------------------------------ 2. a mismatch blocks official generation


def test_matching_hash_does_not_raise(conn):
    seed_calibration_report(conn)
    seed_lock(conn, strategy_version=2, config_hash_value="abc123")
    verify_frozen_config_lock(conn, CFG, running_hash="abc123")  # must not raise


def test_mismatched_hash_refuses_with_a_loud_error(conn):
    seed_calibration_report(conn)
    seed_lock(conn, strategy_version=2, config_hash_value="the-locked-hash")
    with pytest.raises(SystemExit, match="REFUSING to generate official candidates"):
        verify_frozen_config_lock(conn, CFG, running_hash="a-different-hash")


def test_mismatch_error_names_both_hashes_and_the_justifying_report(conn):
    seed_calibration_report(conn, report_id="calib-abc")
    seed_lock(conn, strategy_version=2, config_hash_value="locked-hash-value", report_id="calib-abc")
    with pytest.raises(SystemExit) as excinfo:
        verify_frozen_config_lock(conn, CFG, running_hash="running-hash-value")
    message = str(excinfo.value)
    assert "locked-hash-value" in message
    assert "running-hash-value" in message
    assert "calib-abc" in message


def test_no_lock_row_at_all_refuses(conn):
    with pytest.raises(SystemExit, match="no frozen_config_lock row"):
        verify_frozen_config_lock(conn, CFG, running_hash="anything")


def test_a_lock_for_a_different_strategy_version_does_not_satisfy_this_one(conn):
    """A lock is per strategy_version, never inherited across a bump."""
    seed_calibration_report(conn)
    seed_lock(conn, strategy_version=1, config_hash_value="whatever")
    with pytest.raises(SystemExit, match="no frozen_config_lock row"):
        verify_frozen_config_lock(conn, CFG, running_hash="whatever")


# ------------------------------------------------------------ append-only


def test_frozen_config_lock_rejects_update(conn):
    import sqlite3

    seed_calibration_report(conn)
    seed_lock(conn, strategy_version=2, config_hash_value="original")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE frozen_config_lock SET config_hash = 'tampered' WHERE strategy_version = 2"
        )


def test_frozen_config_lock_rejects_delete(conn):
    import sqlite3

    seed_calibration_report(conn)
    seed_lock(conn, strategy_version=2, config_hash_value="original")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM frozen_config_lock WHERE strategy_version = 2")


# --------------------------------------------------- against the real lock


def test_the_real_locked_config_hash_matches_the_file_on_disk():
    """The lock inserted for this session's real freeze (strategy_version=2,
    composite_threshold=55) must still match config.frozen.json as it
    stands, or every official run would refuse."""
    import migrate as real_migrate

    conn = real_migrate.connect()
    try:
        row = conn.execute(
            "SELECT config_hash FROM frozen_config_lock WHERE strategy_version = 2"
        ).fetchone()
        if row is None:
            pytest.skip("no real lock row yet")
        assert row["config_hash"] == config_hash()
    finally:
        conn.close()
