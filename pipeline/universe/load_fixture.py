"""Load the version-controlled fixture manifest into the database.

The manifest (fixture_manifest.json, sitting next to this file) is the
source of truth for WHICH securities are in the fixture and WHY. Everything
else - names, CIKs, SIC codes, instrument type - is resolved from SEC and
Nasdaq Trader at load time, never typed in by hand.

Re-running is safe: securities are matched on (cik, share_class) so the same
security_id is reused instead of a second row being created.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from universe import identity  # noqa: E402
from universe.classify import Classification, classify, classify_from_security_title  # noqa: E402
from universe.nasdaq_client import NasdaqClient  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ, sec_symbol, utc_now_iso, utc_today  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "fixture_manifest.json"


class FixtureError(RuntimeError):
    pass


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FixtureError(f"Fixture manifest not found: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for required in ("manifest_version", "securities"):
        if required not in manifest:
            raise FixtureError(f"Manifest is missing '{required}'")
    for entry in manifest["securities"]:
        for required in ("symbol", "category", "inclusion_reason"):
            if not entry.get(required):
                raise FixtureError(f"Manifest entry {entry} is missing '{required}'")
    return manifest


def find_existing_security(
    conn: sqlite3.Connection, cik: str | None, share_class: str | None, name: str
) -> int | None:
    """Match on (cik, share_class) first; fall back to name when there is no CIK."""
    if cik:
        row = conn.execute(
            "SELECT security_id FROM securities WHERE cik = ? AND share_class IS ?",
            (cik, share_class),
        ).fetchone()
        if row:
            return int(row[0])
        return None
    row = conn.execute(
        "SELECT security_id FROM securities WHERE cik IS NULL AND name = ?", (name,)
    ).fetchone()
    return int(row[0]) if row else None


def resolve_entry(
    entry: dict[str, Any],
    sec: SecClient,
    tickers: dict[str, dict[str, Any]],
    listings: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve one manifest entry into everything the securities table needs."""
    symbol = entry["symbol"].upper()
    listing = listings.get(symbol)

    ticker_record = tickers.get(sec_symbol(symbol)) or tickers.get(symbol)
    cik = entry.get("cik") or (ticker_record or {}).get("cik")

    submissions: dict[str, Any] | None = None
    if cik:
        try:
            submissions = sec.fetch_submissions(cik)
        except Exception:  # noqa: BLE001
            submissions = None

    observation_date = utc_today()

    if listing:
        classification = classify(listing)
        name = listing["security_name"]
        exchange = listing["exchange"]
        is_active = True
        delisted_date = None
        valid_from = entry.get("valid_from", observation_date)
        valid_to = None
    else:
        # Not in either exchange directory file. Classify from the registered
        # security title on the most recent annual report cover page.
        title = form = filing_date = None
        if cik:
            title, form, filing_date = sec.fetch_registered_security_title(cik)
        classification = classify_from_security_title(title)
        if title and form:
            classification = Classification(
                classification.security_type,
                classification.confidence,
                f"{classification.source}|{form}@{filing_date}",
                classification.share_class,
            )
        name = (submissions or {}).get("name") or entry.get("name") or symbol
        exchange = (submissions or {}).get("exchanges") or ["NOT LISTED"]
        exchange = exchange[0] if isinstance(exchange, list) and exchange else "NOT LISTED"
        is_active = False
        # We cannot observe the official delisting date from these sources, so
        # this records the first date our pipeline saw the symbol absent from
        # both exchange directory files. Documented, not guessed.
        delisted_date = entry.get("delisted_date", observation_date)
        valid_from = entry.get("valid_from") or filing_date or observation_date
        valid_to = delisted_date

    return {
        "symbol": symbol,
        "cik": cik,
        "name": name,
        "exchange": exchange,
        "classification": classification,
        "sic_code": (submissions or {}).get("sic") or None,
        "is_active": is_active,
        "delisted_date": delisted_date,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def load(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
    sec: SecClient,
    nasdaq: NasdaqClient,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    run_id = f"fixture-{uuid.uuid4().hex[:12]}"
    started_at = utc_now_iso()
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES (?, 'fixture_load', ?, 'running', ?)",
        (run_id, started_at, manifest["manifest_version"]),
    )

    errors: list[str] = []
    try:
        tickers = sec.fetch_company_tickers()
        listings = nasdaq.fetch_all_listings()
        _record_source_health(conn, "sec_company_tickers", ok=True)
        _record_source_health(conn, "nasdaq_trader", ok=True)
    except Exception as exc:  # noqa: BLE001
        _record_source_health(conn, "sec_company_tickers", ok=False)
        conn.execute(
            "UPDATE pipeline_runs SET status = 'failed', finished_at = ?, errors_json = ? "
            "WHERE run_id = ?",
            (utc_now_iso(), json.dumps([str(exc)]), run_id),
        )
        raise

    results: list[dict[str, Any]] = []
    for entry in manifest["securities"]:
        try:
            resolved = resolve_entry(entry, sec, tickers, listings)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{entry['symbol']}: {exc}")
            continue

        classification = resolved["classification"]
        security_id = find_existing_security(
            conn, resolved["cik"], classification.share_class, resolved["name"]
        )
        now = utc_now_iso()
        if security_id is None:
            security_id = identity.create_security(
                conn,
                name=resolved["name"],
                cik=resolved["cik"],
                share_class=classification.share_class,
                security_type=classification.security_type,
                classification_confidence=classification.confidence,
                classification_source=classification.source,
                sic_code=resolved["sic_code"],
                first_seen=now,
                last_seen=now,
                is_active=resolved["is_active"],
                delisted_date=resolved["delisted_date"],
            )
        else:
            conn.execute(
                """
                UPDATE securities
                   SET name = ?, security_type = ?, classification_confidence = ?,
                       classification_source = ?, sic_code = ?, last_seen = ?,
                       is_active = ?, delisted_date = ?
                 WHERE security_id = ?
                """,
                (
                    resolved["name"],
                    classification.security_type,
                    classification.confidence,
                    classification.source,
                    resolved["sic_code"],
                    now,
                    1 if resolved["is_active"] else 0,
                    resolved["delisted_date"],
                    security_id,
                ),
            )

        identity.add_listing(
            conn,
            security_id=security_id,
            symbol=resolved["symbol"],
            exchange=resolved["exchange"],
            valid_from=resolved["valid_from"],
            valid_to=resolved["valid_to"],
            is_primary=True,
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO fixture_manifest
                (security_id, symbol_at_selection, inclusion_reason, category,
                 added_at, manifest_version)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                security_id,
                resolved["symbol"],
                entry["inclusion_reason"],
                entry["category"],
                now,
                manifest["manifest_version"],
            ),
        )

        row = {
            "security_id": security_id,
            "symbol": resolved["symbol"],
            "type": classification.security_type,
            "confidence": classification.confidence,
            "source": classification.source,
            "category": entry["category"],
            "cik": resolved["cik"],
            "name": resolved["name"],
        }
        results.append(row)
        if verbose:
            print(
                f"{row['symbol']:<8} id={security_id:<4} {row['type']:<15} "
                f"{row['confidence']:<7} {row['category']}"
            )

    conn.execute(
        "UPDATE pipeline_runs SET status = ?, finished_at = ?, records_written = ?, "
        "errors_json = ? WHERE run_id = ?",
        (
            "success" if not errors else "partial",
            utc_now_iso(),
            len(results),
            json.dumps(errors) if errors else None,
            run_id,
        ),
    )
    if errors:
        print(f"\n{len(errors)} entries failed:", file=sys.stderr)
        for message in errors:
            print(f"  {message}", file=sys.stderr)
    return results


def _record_source_health(conn: sqlite3.Connection, source_name: str, ok: bool) -> None:
    now = utc_now_iso()
    if ok:
        conn.execute(
            """
            INSERT INTO source_health (source_name, last_success, consecutive_failures, staleness_hours)
            VALUES (?, ?, 0, 0)
            ON CONFLICT (source_name) DO UPDATE
               SET last_success = excluded.last_success,
                   consecutive_failures = 0,
                   staleness_hours = 0
            """,
            (source_name, now),
        )
    else:
        conn.execute(
            """
            INSERT INTO source_health (source_name, last_error, consecutive_failures)
            VALUES (?, ?, 1)
            ON CONFLICT (source_name) DO UPDATE
               SET last_error = excluded.last_error,
                   consecutive_failures = source_health.consecutive_failures + 1
            """,
            (source_name, now),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the fixture manifest")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    args = parser.parse_args(argv)

    load_dotenv_into_environ()
    manifest = load_manifest(Path(args.manifest))
    conn = migrate.connect(Path(args.db))
    try:
        conn.execute("BEGIN")
        results = load(conn, manifest, SecClient(), NasdaqClient())
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    unknown = [r for r in results if r["type"] == "unknown"]
    print(f"\nloaded {len(results)} securities, {len(unknown)} classified unknown")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
