"""One-time backfill: populate sic_code for existing securities that have a
CIK but no sic_code on file.

pool_loader.py never called SEC submissions before this fix (see its
resolve_sic_code), so every S1/S2 pool-only security landed with sic_code
NULL and fell into scoring.cohorts.UNCLASSIFIED regardless of its real
industry. Fixture securities already carry sic_code from load_fixture.py's
own submissions call, so this backfill is effectively pool-scoped without
needing to say so explicitly -- it just targets what is actually missing.

Safe to run more than once: it only ever touches rows where sic_code IS NULL,
so a second run is a no-op except for whatever still failed to resolve (no
CIK, submissions down, or the CIK genuinely carries no SIC on file) on the
prior pass.

Each security's update is its own auto-committed statement (this connection
runs with isolation_level=None and nothing here opens an explicit BEGIN), so
a crash partway through loses at most the security in flight -- the same
per-item durability principle S2's orchestrator uses, without needing its
resumability machinery for what is a few-minute one-time job.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ, utc_now_iso  # noqa: E402


def find_unresolved(conn) -> list[tuple[int, str]]:
    """(security_id, cik) for every security with a CIK but no sic_code,
    oldest security_id first so re-running after a partial failure resumes
    in the same order."""
    rows = conn.execute(
        "SELECT security_id, cik FROM securities "
        "WHERE cik IS NOT NULL AND sic_code IS NULL "
        "ORDER BY security_id"
    ).fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def backfill(conn, sec: SecClient, verbose: bool = True) -> dict[str, int]:
    unresolved = find_unresolved(conn)
    run_id = f"sicbackfill-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES (?, 'sic_backfill', ?, 'running', 'one-time')",
        (run_id, utc_now_iso()),
    )

    cache: dict[str, str | None] = {}
    updated = 0
    no_sic_on_file = 0
    failed = 0

    for security_id, cik in unresolved:
        if cik not in cache:
            try:
                submissions = sec.fetch_submissions(cik)
                cache[cik] = submissions.get("sic") or None
            except Exception as exc:  # noqa: BLE001
                cache[cik] = None
                failed += 1
                if verbose:
                    print(f"  security_id={security_id} cik={cik}: fetch failed ({exc})")
                continue

        sic_code = cache[cik]
        if not sic_code:
            no_sic_on_file += 1
            if verbose:
                print(f"  security_id={security_id} cik={cik}: no SIC on file")
            continue

        conn.execute(
            "UPDATE securities SET sic_code = ? WHERE security_id = ?",
            (sic_code, security_id),
        )
        updated += 1
        if verbose:
            print(f"  security_id={security_id} cik={cik}: sic_code={sic_code}")

    conn.execute(
        "UPDATE pipeline_runs SET status = ?, finished_at = ?, records_written = ? "
        "WHERE run_id = ?",
        (
            "success" if failed == 0 else "partial",
            utc_now_iso(),
            updated,
            run_id,
        ),
    )
    return {
        "candidates": len(unresolved),
        "updated": updated,
        "no_sic_on_file": no_sic_on_file,
        "failed": failed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill sic_code for securities that have a CIK but no SIC on file"
    )
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    args = parser.parse_args(argv)

    load_dotenv_into_environ()
    sec = SecClient()
    conn = migrate.connect(Path(args.db))
    try:
        summary = backfill(conn, sec)
    finally:
        conn.close()

    print(
        f"\n{summary['candidates']} candidates, {summary['updated']} updated, "
        f"{summary['no_sic_on_file']} had no SIC on file, {summary['failed']} fetch failures"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
