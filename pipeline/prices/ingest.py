"""Price ingestion, revision detection, and dataset versioning.

The contract:

  * A vendor correction is NEVER silently absorbed. When a re-fetch disagrees
    with a stored bar, the complete old and new OHLCV is written to
    price_revisions, the row's revision counter increments, a new global
    price_dataset_version is created, and only then is the canonical row updated.

  * Re-running on unchanged data changes nothing except last_verified_at. No new
    dataset version is created.

  * Prices are raw. Nothing here adjusts for splits or dividends.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from prices.base import CorporateActionRecord, PriceBar, PriceProvider, PriceProviderError  # noqa: E402
from prices.registry import get_provider  # noqa: E402

# A price difference smaller than this is float noise, not a vendor correction.
PRICE_TOLERANCE = 0.005  # half a cent
DEFAULT_YEARS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plus_one_second(utc_iso: str) -> str:
    from datetime import timedelta

    moment = datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (moment + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class IngestReport:
    securities_attempted: int = 0
    securities_with_data: int = 0
    securities_without_data: list[str] = None  # type: ignore[assignment]
    rows_inserted: int = 0
    rows_unchanged: int = 0
    revisions_detected: int = 0
    actions_written: int = 0
    dataset_version_before: int | None = None
    dataset_version_after: int | None = None
    earliest_date: str | None = None
    latest_date: str | None = None
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.securities_without_data is None:
            self.securities_without_data = []
        if self.errors is None:
            self.errors = []


def current_dataset_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(dataset_version) FROM price_dataset_versions").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def create_dataset_version(
    conn: sqlite3.Connection,
    provider: str,
    reason: str,
    changed_row_count: int,
    run_id: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO price_dataset_versions (created_at, provider, reason, changed_row_count, run_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (utc_now(), provider, reason, changed_row_count, run_id),
    )
    return int(cursor.lastrowid)


def _differs(old: float | None, new: float | None, tolerance: float = PRICE_TOLERANCE) -> bool:
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(float(old) - float(new)) > tolerance


def bar_differs(stored: sqlite3.Row, bar: PriceBar) -> bool:
    """True when the vendor now reports something materially different."""
    if any(
        _differs(stored[field], value)
        for field, value in (
            ("open", bar.open),
            ("high", bar.high),
            ("low", bar.low),
            ("close", bar.close),
        )
    ):
        return True
    # Volume is an integer count, so any change at all is a real change.
    old_volume = stored["volume"]
    if old_volume is None and bar.volume is None:
        return False
    if old_volume is None or bar.volume is None:
        return True
    return int(old_volume) != int(bar.volume)


def fixture_securities(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """The fixture, as (security_id, symbol) using each security's current symbol."""
    rows = conn.execute(
        """
        SELECT f.security_id, COALESCE(l.symbol, f.symbol_at_selection) AS symbol
          FROM fixture_manifest f
          LEFT JOIN listings l
                 ON l.security_id = f.security_id AND l.valid_to IS NULL
         GROUP BY f.security_id
         ORDER BY f.security_id
        """
    ).fetchall()
    return [(int(row["security_id"]), row["symbol"]) for row in rows]


def _record_provenance(
    conn: sqlite3.Connection, security_id: int, provider: str, switch_reason: str | None = None
) -> None:
    """Open a provenance window if this security has none open for this provider."""
    existing = conn.execute(
        "SELECT 1 FROM price_series_provenance "
        "WHERE security_id = ? AND provider = ? AND valid_to IS NULL",
        (security_id, provider),
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT OR IGNORE INTO price_series_provenance "
        "(security_id, provider, valid_from, valid_to, switch_reason) VALUES (?, ?, ?, NULL, ?)",
        (security_id, provider, utc_now(), switch_reason),
    )


def _write_actions(
    conn: sqlite3.Connection, security_id: int, provider: str, actions: Iterable[CorporateActionRecord]
) -> int:
    written = 0
    for action in actions:
        conn.execute(
            """
            INSERT INTO corporate_actions
                (security_id, ex_date, action_type, ratio, cash_amount, provider,
                 requires_manual_review)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (security_id, ex_date, action_type) DO UPDATE SET
                ratio = excluded.ratio,
                cash_amount = excluded.cash_amount,
                provider = excluded.provider,
                requires_manual_review = excluded.requires_manual_review
            """,
            (
                security_id,
                action.ex_date,
                action.action_type,
                action.ratio,
                action.cash_amount,
                provider,
                1 if action.requires_manual_review else 0,
            ),
        )
        written += 1
    return written


def ingest_securities(
    conn: sqlite3.Connection,
    provider: PriceProvider,
    securities: Sequence[tuple[int, str]],
    years: int = DEFAULT_YEARS,
    run_id: str | None = None,
    verbose: bool = True,
) -> IngestReport:
    """Fetch, compare, audit, and store. Returns a summary."""
    report = IngestReport()
    report.dataset_version_before = current_dataset_version(conn)
    report.securities_attempted = len(securities)

    inserts: list[tuple] = []
    revisions: list[tuple[int, str, sqlite3.Row, PriceBar]] = []
    all_actions: list[tuple[int, list[CorporateActionRecord]]] = []

    for security_id, symbol in securities:
        try:
            bars = provider.fetch_daily_bars(symbol, years)
            actions = provider.fetch_corporate_actions(symbol, years)
        except PriceProviderError as exc:
            report.errors.append(f"{symbol}: {exc}")
            continue

        if not bars:
            report.securities_without_data.append(symbol)
            if verbose:
                print(f"{symbol:<8} no data from {provider.name}")
            continue

        report.securities_with_data += 1
        all_actions.append((security_id, actions))

        stored = {
            row["date"]: row
            for row in conn.execute(
                "SELECT * FROM prices WHERE security_id = ?", (security_id,)
            )
        }

        changed_here = 0
        for bar in bars:
            existing = stored.get(bar.date)
            if existing is None:
                inserts.append((security_id, bar))
            elif bar_differs(existing, bar):
                revisions.append((security_id, bar.date, existing, bar))
                changed_here += 1
            else:
                report.rows_unchanged += 1

            if report.earliest_date is None or bar.date < report.earliest_date:
                report.earliest_date = bar.date
            if report.latest_date is None or bar.date > report.latest_date:
                report.latest_date = bar.date

        if verbose:
            new_here = sum(1 for sid, _ in inserts if sid == security_id)
            print(
                f"{symbol:<8} bars={len(bars):<5} new={new_here:<5} "
                f"revisions={changed_here:<3} actions={len(actions)}"
            )

    report.rows_inserted = len(inserts)
    report.revisions_detected = len(revisions)

    changed_rows = len(inserts) + len(revisions)
    if changed_rows == 0:
        # Nothing moved. Touch last_verified_at only, and create NO new version.
        now = utc_now()
        for security_id, _ in securities:
            conn.execute(
                "UPDATE prices SET last_verified_at = ? WHERE security_id = ?", (now, security_id)
            )
        report.dataset_version_after = report.dataset_version_before
    else:
        reason_parts = []
        if inserts:
            reason_parts.append(f"{len(inserts)} new rows")
        if revisions:
            reason_parts.append(f"{len(revisions)} vendor corrections")
        version = create_dataset_version(
            conn, provider.name, "; ".join(reason_parts), changed_rows, run_id
        )
        report.dataset_version_after = version
        now = utc_now()

        for security_id, bar in inserts:
            conn.execute(
                """
                INSERT INTO prices (security_id, date, open, high, low, close, volume,
                                    provider, first_seen_at, last_verified_at, revision,
                                    price_data_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    security_id, bar.date, bar.open, bar.high, bar.low, bar.close,
                    bar.volume, provider.name, now, now, version,
                ),
            )

        for security_id, date, existing, bar in revisions:
            new_revision = int(existing["revision"]) + 1
            conn.execute(
                """
                INSERT INTO price_revisions
                    (security_id, date, revision,
                     old_open, old_high, old_low, old_close, old_volume,
                     new_open, new_high, new_low, new_close, new_volume,
                     detected_at, accepted_at, provider,
                     price_data_version_before, price_data_version_after)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    security_id, date, new_revision,
                    existing["open"], existing["high"], existing["low"],
                    existing["close"], existing["volume"],
                    bar.open, bar.high, bar.low, bar.close, bar.volume,
                    now,
                    # Release 1 auto-accepts vendor corrections, but only after
                    # the complete before/after pair is on record above.
                    now,
                    provider.name,
                    existing["price_data_version"], version,
                ),
            )
            conn.execute(
                """
                UPDATE prices
                   SET open = ?, high = ?, low = ?, close = ?, volume = ?,
                       last_verified_at = ?, revision = ?, price_data_version = ?
                 WHERE security_id = ? AND date = ?
                """,
                (
                    bar.open, bar.high, bar.low, bar.close, bar.volume,
                    now, new_revision, version, security_id, date,
                ),
            )

        for security_id, _ in securities:
            conn.execute(
                "UPDATE prices SET last_verified_at = ? WHERE security_id = ? "
                "AND last_verified_at < ?",
                (now, security_id, now),
            )

    for security_id, actions in all_actions:
        report.actions_written += _write_actions(conn, security_id, provider.name, actions)
        _record_provenance(conn, security_id, provider.name)

    return report


def switch_provider(
    conn: sqlite3.Connection,
    new_provider: PriceProvider,
    securities: Sequence[tuple[int, str]],
    switch_reason: str,
    years: int = DEFAULT_YEARS,
) -> IngestReport:
    """Move to a different provider without ever splicing two series together.

    Deletes the old provider's rows for each security, closes its provenance
    window, then refetches the entire history from the new provider.
    """
    now = utc_now()
    for security_id, _ in securities:
        # A provenance window must have positive duration. Timestamps are stored
        # to the second, so opening and closing a window inside the same second
        # would produce valid_to == valid_from and fail the CHECK. Close it one
        # second after it opened instead of pretending the window never existed.
        for row in conn.execute(
            "SELECT provider, valid_from FROM price_series_provenance "
            "WHERE security_id = ? AND valid_to IS NULL",
            (security_id,),
        ).fetchall():
            valid_to = now if now > row["valid_from"] else _plus_one_second(row["valid_from"])
            conn.execute(
                "UPDATE price_series_provenance SET valid_to = ?, switch_reason = ? "
                "WHERE security_id = ? AND provider = ? AND valid_from = ?",
                (valid_to, switch_reason, security_id, row["provider"], row["valid_from"]),
            )
        conn.execute(
            "DELETE FROM prices WHERE security_id = ? AND provider <> ?",
            (security_id, new_provider.name),
        )
    return ingest_securities(conn, new_provider, securities, years=years)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest raw daily prices")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    parser.add_argument("--symbols", nargs="*", help="limit to these symbols")
    parser.add_argument(
        "--pool",
        default=None,
        help="ingest a universe_candidate_pool version instead of the Phase F fixture "
        "(e.g. s1-sample-v1); does not touch fixture_manifest",
    )
    args = parser.parse_args(argv)

    conn = migrate.connect(Path(args.db))
    provider = get_provider()
    run_id = f"prices-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'price_ingest', ?, 'running', ?)",
            (run_id, utc_now(), provider.name),
        )
        if args.pool:
            from universe.pool import pool_securities

            securities = [(row["security_id"], row["symbol"]) for row in pool_securities(conn, args.pool)]
        else:
            securities = fixture_securities(conn)
        if args.symbols:
            wanted = {s.upper() for s in args.symbols}
            securities = [(sid, sym) for sid, sym in securities if sym.upper() in wanted]

        report = ingest_securities(conn, provider, securities, years=args.years, run_id=run_id)

        conn.execute(
            "UPDATE pipeline_runs SET status = ?, finished_at = ?, records_written = ?, "
            "errors_json = ? WHERE run_id = ?",
            (
                "success" if not report.errors else "partial",
                utc_now(),
                report.rows_inserted + report.revisions_detected,
                json.dumps(report.errors) if report.errors else None,
                run_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO source_health (source_name, last_success, consecutive_failures, staleness_hours)
            VALUES (?, ?, 0, 0)
            ON CONFLICT (source_name) DO UPDATE
               SET last_success = excluded.last_success, consecutive_failures = 0, staleness_hours = 0
            """,
            (f"prices:{provider.name}", utc_now()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print("\n=== ingest summary ===")
    print(f"securities attempted : {report.securities_attempted}")
    print(f"securities with data : {report.securities_with_data}")
    print(f"no data              : {', '.join(report.securities_without_data) or 'none'}")
    print(f"date range           : {report.earliest_date} .. {report.latest_date}")
    print(f"rows inserted        : {report.rows_inserted}")
    print(f"rows unchanged       : {report.rows_unchanged}")
    print(f"revisions detected   : {report.revisions_detected}")
    print(f"corporate actions    : {report.actions_written}")
    print(f"dataset version      : {report.dataset_version_before} -> {report.dataset_version_after}")
    if report.errors:
        print(f"errors               : {len(report.errors)}")
        for message in report.errors[:10]:
            print(f"  {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
