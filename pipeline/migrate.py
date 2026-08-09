"""Versioned SQLite migration runner.

Migration files live in /migrations and are named:

    NNN_description.up.sql     applies the change
    NNN_description.down.sql   reverses it

Rules this runner enforces:
  * Migrations apply in ascending numeric order.
  * Every applied version is recorded in schema_migrations, so running "up"
    twice is a no-op (idempotent).
  * Each migration runs inside one transaction: it either fully applies and is
    recorded, or nothing changes.
  * applied_at is stored as UTC ISO-8601 with a trailing Z.

Usage (from the repo root, with the venv active):
    python pipeline/migrate.py status
    python pipeline/migrate.py up
    python pipeline/migrate.py down            # roll back the newest migration
    python pipeline/migrate.py down --to 000   # roll back everything
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "stockbot.db"

FILENAME_RE = re.compile(r"^(?P<version>\d{3})_(?P<slug>[a-z0-9_]+)\.(?P<direction>up|down)\.sql$")


class MigrationError(RuntimeError):
    """Raised when migrations are malformed or cannot be applied."""


def utc_now_iso() -> str:
    """Current time as UTC ISO-8601, e.g. 2026-07-29T13:45:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Migration:
    def __init__(self, version: str, slug: str, up_path: Path, down_path: Path):
        self.version = version
        self.slug = slug
        self.up_path = up_path
        self.down_path = down_path

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Migration {self.version}_{self.slug}>"


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Read the migrations directory and return migrations in version order."""
    if not migrations_dir.is_dir():
        raise MigrationError(f"Migrations directory not found: {migrations_dir}")

    found: dict[str, dict[str, Path]] = {}
    slugs: dict[str, str] = {}

    for path in sorted(migrations_dir.iterdir()):
        if path.suffix != ".sql":
            continue
        match = FILENAME_RE.match(path.name)
        if not match:
            raise MigrationError(
                f"Bad migration filename: {path.name}. "
                "Expected NNN_snake_case_name.up.sql or NNN_snake_case_name.down.sql"
            )
        version = match.group("version")
        slug = match.group("slug")
        direction = match.group("direction")

        if version in slugs and slugs[version] != slug:
            raise MigrationError(
                f"Version {version} has two different names: "
                f"{slugs[version]} and {slug}"
            )
        slugs[version] = slug
        found.setdefault(version, {})[direction] = path

    migrations: list[Migration] = []
    for version in sorted(found):
        parts = found[version]
        if "up" not in parts:
            raise MigrationError(f"Migration {version} is missing its .up.sql file")
        if "down" not in parts:
            raise MigrationError(f"Migration {version} is missing its .down.sql file")
        migrations.append(Migration(version, slugs[version], parts["up"], parts["down"]))

    if not migrations:
        raise MigrationError(f"No migrations found in {migrations_dir}")
    return migrations


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the database, create the folder if needed, and ensure the ledger table."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # timeout=30 (Python's default is 5s): the Aug 9 resume run hit
    # "database is locked" twice in a row from ordinary brief contention
    # (a child orchestrate process's file lock not yet released, a
    # concurrent read) -- 30s rides that out without masking a genuine
    # deadlock for long. Every pipeline script shares this one connect(),
    # so this is the actual root-cause fix, not a per-call-site patch.
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.isolation_level = None  # we manage BEGIN/COMMIT ourselves
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: a reader (this session's status-heartbeat, a manual inspection
    # query) no longer blocks on -- or gets blocked by -- a concurrent
    # writer (an orchestrate child mid-batch). Confirmed needed: even a 30s
    # busy timeout still hit "database is locked" under the daily.py
    # heartbeat + orchestrate write pattern on the default rollback-journal
    # mode. Standard, safe for a single-machine app; only side effect is
    # -wal/-shm sidecar files next to stockbot.db, both already covered by
    # data/ being gitignored wholesale.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    return conn


def applied_versions(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return [row["version"] for row in rows]


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into complete statements.

    Uses sqlite3.complete_statement so quoted text and BEGIN...END trigger
    bodies are handled correctly, instead of naively splitting on ';'.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    leftover = buffer.strip()
    if leftover and not leftover.startswith("--"):
        raise MigrationError(f"Unterminated SQL statement (missing ';'):\n{leftover}")
    return statements


def _run_script(conn: sqlite3.Connection, sql: str) -> None:
    for statement in split_statements(sql):
        conn.execute(statement)


def apply_up(conn: sqlite3.Connection, migration: Migration) -> None:
    sql = migration.up_path.read_text(encoding="utf-8")
    conn.execute("BEGIN")
    try:
        _run_script(conn, sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (migration.version, utc_now_iso()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def apply_down(conn: sqlite3.Connection, migration: Migration) -> None:
    sql = migration.down_path.read_text(encoding="utf-8")
    conn.execute("BEGIN")
    try:
        _run_script(conn, sql)
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (migration.version,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def migrate_up(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> list[str]:
    """Apply every migration not yet recorded. Returns versions applied now."""
    migrations = migrations if migrations is not None else discover_migrations()
    done = set(applied_versions(conn))
    newly_applied: list[str] = []
    for migration in migrations:
        if migration.version in done:
            continue
        apply_up(conn, migration)
        newly_applied.append(migration.version)
    return newly_applied


def migrate_down(
    conn: sqlite3.Connection,
    target: str | None = None,
    migrations: list[Migration] | None = None,
) -> list[str]:
    """Roll back migrations newest-first.

    target=None  -> roll back exactly one migration
    target="000" -> roll back everything
    target="001" -> roll back until 001 is the newest still applied
    """
    migrations = migrations if migrations is not None else discover_migrations()
    by_version = {m.version: m for m in migrations}
    done = sorted(applied_versions(conn), reverse=True)

    rolled_back: list[str] = []
    for version in done:
        if target is None and rolled_back:
            break
        if target is not None and version <= target:
            break
        migration = by_version.get(version)
        if migration is None:
            raise MigrationError(
                f"Version {version} is recorded as applied but its files are missing. "
                "Restore the migration files before rolling back."
            )
        apply_down(conn, migration)
        rolled_back.append(version)
    return rolled_back


def status(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> list[dict]:
    migrations = migrations if migrations is not None else discover_migrations()
    rows = {
        row["version"]: row["applied_at"]
        for row in conn.execute("SELECT version, applied_at FROM schema_migrations")
    }
    return [
        {
            "version": m.version,
            "name": m.slug,
            "applied": m.version in rows,
            "applied_at": rows.get(m.version),
        }
        for m in migrations
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stockbot migration runner")
    parser.add_argument("command", choices=["up", "down", "status"])
    parser.add_argument("--to", dest="target", default=None,
                        help="down only: roll back until this version is the newest applied "
                             "(use 000 to roll back everything)")
    parser.add_argument("--db", dest="db_path", default=str(DEFAULT_DB_PATH),
                        help=f"SQLite file (default {DEFAULT_DB_PATH})")
    args = parser.parse_args(argv)

    conn = connect(Path(args.db_path))
    try:
        if args.command == "up":
            applied = migrate_up(conn)
            if applied:
                print(f"Applied: {', '.join(applied)}")
            else:
                print("Nothing to apply. Database is up to date.")
        elif args.command == "down":
            rolled = migrate_down(conn, target=args.target)
            if rolled:
                print(f"Rolled back: {', '.join(rolled)}")
            else:
                print("Nothing to roll back.")
        else:
            for row in status(conn):
                mark = "applied" if row["applied"] else "PENDING"
                when = row["applied_at"] or "-"
                print(f"{row['version']}  {row['name']:<28} {mark:<8} {when}")
        return 0
    except MigrationError as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
