"""Migrations must apply from empty, roll back cleanly, and be idempotent."""

import sqlite3

import pytest

import migrate


def schema_fingerprint(conn: sqlite3.Connection) -> list[tuple]:
    """Every table/index definition, sorted. Two equal fingerprints = same schema."""
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return [(r["type"], r["name"], r["sql"]) for r in rows]


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def test_discovery_finds_001_with_both_directions():
    migrations = migrate.discover_migrations()
    versions = [m.version for m in migrations]
    assert "001" in versions
    for m in migrations:
        assert m.up_path.is_file()
        assert m.down_path.is_file()


def test_applies_cleanly_from_empty(conn):
    applied = migrate.migrate_up(conn)
    assert "001" in applied

    names = table_names(conn)
    assert {"pipeline_runs", "source_health", "schema_migrations"} <= names

    recorded = migrate.applied_versions(conn)
    assert recorded == [m.version for m in migrate.discover_migrations()]


def test_applied_at_is_utc_iso(conn):
    migrate.migrate_up(conn)
    row = conn.execute(
        "SELECT applied_at FROM schema_migrations WHERE version = '001'"
    ).fetchone()
    stamp = row["applied_at"]
    assert stamp.endswith("Z"), f"applied_at must be UTC with trailing Z, got {stamp!r}"
    assert len(stamp) == 20


def test_up_is_idempotent(conn):
    all_versions = [m.version for m in migrate.discover_migrations()]
    first = migrate.migrate_up(conn)
    fingerprint = schema_fingerprint(conn)

    second = migrate.migrate_up(conn)
    assert second == [], "second up must apply nothing"
    assert first == all_versions
    assert schema_fingerprint(conn) == fingerprint
    assert migrate.applied_versions(conn) == all_versions


def test_down_rolls_back_cleanly(conn):
    all_versions = [m.version for m in migrate.discover_migrations()]
    migrate.migrate_up(conn)
    rolled = migrate.migrate_down(conn, target="000")
    # Rollback runs newest first.
    assert rolled == sorted(all_versions, reverse=True)

    names = table_names(conn)
    assert "pipeline_runs" not in names
    assert "source_health" not in names
    assert "schema_migrations" in names  # ledger survives by design
    assert migrate.applied_versions(conn) == []


def test_down_then_up_restores_identical_schema(conn):
    migrate.migrate_up(conn)
    before = schema_fingerprint(conn)

    migrate.migrate_down(conn, target="000")
    migrate.migrate_up(conn)
    after = schema_fingerprint(conn)

    assert after == before


def test_down_with_no_target_rolls_back_one(conn):
    migrate.migrate_up(conn)
    rolled = migrate.migrate_down(conn)
    assert len(rolled) == 1


def test_down_on_empty_database_is_safe(conn):
    assert migrate.migrate_down(conn, target="000") == []


def test_status_reports_pending_then_applied(conn):
    before = migrate.status(conn)
    assert all(row["applied"] is False for row in before)

    migrate.migrate_up(conn)
    after = {row["version"]: row for row in migrate.status(conn)}
    assert after["001"]["applied"] is True
    assert after["001"]["applied_at"].endswith("Z")


def test_pipeline_runs_rejects_unknown_status(conn):
    migrate.migrate_up(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status) "
            "VALUES ('r1', 'ingest', '2026-07-29T00:00:00Z', 'banana')"
        )


def test_bad_migration_filename_is_rejected(tmp_path):
    (tmp_path / "oops.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(migrate.MigrationError, match="Bad migration filename"):
        migrate.discover_migrations(tmp_path)


def test_missing_down_file_is_rejected(tmp_path):
    (tmp_path / "002_thing.up.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(migrate.MigrationError, match="missing its .down.sql"):
        migrate.discover_migrations(tmp_path)
