"""S1 universe candidate pool: discovery and loading.

Separate from load_fixture.py on purpose. The fixture is Phase F's frozen
50-security engineering set, recorded in fixture_manifest. This module builds
a DIFFERENT, non-official pool -- candidates for the full rules-based universe
-- and records it in universe_candidate_pool (migration 016), never touching
fixture_manifest.

Discovery path:
  1. Pull the full Nasdaq Trader directory (nasdaqlisted.txt + otherlisted.txt),
     already the same client F2 uses.
  2. Keep only NYSE, Nasdaq or NYSE American listings (the brief's exchange
     rule; NYSE Arca, Cboe BZX and IEX rows in otherlisted.txt are dropped).
  3. Classify every remaining row with the SAME classify.classify() F2 uses --
     no second classifier, no drift from the fixture's rules.
  4. Keep only security_type == 'common_stock' with confidence in
     {high, medium} (config: universe_classification_confidence_min), which is
     exactly classify.RANKABLE_CONFIDENCES -- one bar, reused, not reinvented.
  5. Resolve CIK from SEC company_tickers.json. A candidate with no resolvable
     CIK cannot pass the entry rule ("has a CIK") anyway, so it is dropped here
     with a printed reason rather than loaded and excluded later for the same
     cause twice.
  6. Deterministic stride sample down to the target pool size, sorted by
     symbol first so the sample spans the alphabet (and therefore a spread of
     market caps and sectors) rather than clustering.

At load time, load_pool() also resolves each new (or still-unresolved) security's
sic_code from SEC submissions -- the same call load_fixture.py already makes --
so pool securities get real cohort membership instead of landing in
scoring.cohorts.UNCLASSIFIED for lack of a SIC code that was simply never fetched.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import load_config  # noqa: E402
from universe import identity  # noqa: E402
from universe.classify import RANKABLE_CONFIDENCES, classify  # noqa: E402
from universe.load_fixture import find_existing_security  # noqa: E402
from universe.membership import ALLOWED_EXCHANGES  # noqa: E402
from universe.nasdaq_client import NasdaqClient  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ, sec_symbol, utc_now_iso, utc_today  # noqa: E402

DEFAULT_POOL_VERSION = "s1-sample-v1"
DEFAULT_TARGET_SIZE = 200


def discover_candidates(
    nasdaq: NasdaqClient,
    tickers: dict[str, dict[str, Any]],
    target_size: int = DEFAULT_TARGET_SIZE,
) -> list[dict[str, Any]]:
    """Full directory -> exchange filter -> classify -> confidence filter ->
    CIK resolution -> deterministic stride sample."""
    listings = nasdaq.fetch_all_listings()

    eligible: list[dict[str, Any]] = []
    for symbol, listing in listings.items():
        if listing["exchange"] not in ALLOWED_EXCHANGES:
            continue
        classification = classify(listing)
        if classification.security_type != "common_stock":
            continue
        if classification.confidence not in RANKABLE_CONFIDENCES:
            continue
        ticker_record = tickers.get(sec_symbol(symbol)) or tickers.get(symbol)
        cik = (ticker_record or {}).get("cik")
        if not cik:
            continue
        eligible.append(
            {
                "symbol": symbol,
                "security_name": listing["security_name"],
                "exchange": listing["exchange"],
                "cik": cik,
                "classification": classification,
            }
        )

    eligible.sort(key=lambda row: row["symbol"])
    if len(eligible) <= target_size:
        return eligible

    stride = len(eligible) / target_size
    sampled_indices = sorted({int(i * stride) for i in range(target_size)})
    return [eligible[i] for i in sampled_indices]


def resolve_sic_code(
    conn,
    sec: SecClient,
    security_id: int,
    cik: str | None,
    cache: dict[str, str | None],
) -> None:
    """Fill in sic_code for one security from SEC submissions, if it is
    currently missing. Skips securities that already have a sic_code (from a
    prior fixture load or a prior pool run) so a re-run never re-fetches what
    it already has.

    Same source load_fixture.py already uses (submissions.get("sic")) -- pool
    discovery just never called it, which is why every pool-only security
    landed with sic_code NULL and fell into the SIC-UNKNOWN cohort regardless
    of its real industry.
    """
    if not cik:
        return
    row = conn.execute(
        "SELECT sic_code FROM securities WHERE security_id = ?", (security_id,)
    ).fetchone()
    if row is None or row[0]:
        return
    if cik not in cache:
        try:
            submissions = sec.fetch_submissions(cik)
        except Exception:  # noqa: BLE001
            submissions = None
        cache[cik] = (submissions or {}).get("sic") or None
    sic_code = cache[cik]
    if sic_code:
        conn.execute(
            "UPDATE securities SET sic_code = ? WHERE security_id = ?",
            (sic_code, security_id),
        )


def load_pool(
    conn,
    candidates: list[dict[str, Any]],
    pool_version: str,
    sec: SecClient,
    discovery_source: str = "nasdaq_trader_directory_sample",
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Resolve each candidate into securities/listings, then record it in
    universe_candidate_pool. Reuses an existing security_id when one already
    exists for the (cik, share_class) pair -- same matching rule load_fixture
    uses, so a symbol already in the Phase F fixture is NOT duplicated."""
    run_id = f"pool-{uuid.uuid4().hex[:12]}"
    now = utc_now_iso()
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES (?, 'universe_pool_load', ?, 'running', ?)",
        (run_id, now, pool_version),
    )

    sic_cache: dict[str, str | None] = {}
    results: list[dict[str, Any]] = []
    for entry in candidates:
        classification = entry["classification"]
        existing_id = find_existing_security(
            conn, entry["cik"], classification.share_class, entry["security_name"],
            classification.security_type,
        )
        if existing_id is not None:
            security_id = existing_id
        else:
            security_id = identity.create_security(
                conn,
                name=entry["security_name"],
                cik=entry["cik"],
                share_class=classification.share_class,
                security_type=classification.security_type,
                classification_confidence=classification.confidence,
                classification_source=classification.source,
                sic_code=None,
                first_seen=now,
                last_seen=now,
                is_active=True,
                delisted_date=None,
            )

        resolve_sic_code(conn, sec, security_id, entry["cik"], sic_cache)

        identity.add_listing(
            conn,
            security_id=security_id,
            symbol=entry["symbol"],
            exchange=entry["exchange"],
            valid_from=utc_today(),
            valid_to=None,
            is_primary=True,
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO universe_candidate_pool
                (security_id, symbol_at_discovery, exchange, discovered_at,
                 pool_version, discovery_source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (security_id, entry["symbol"], entry["exchange"], now, pool_version, discovery_source),
        )

        row = {
            "security_id": security_id,
            "symbol": entry["symbol"],
            "cik": entry["cik"],
            "type": classification.security_type,
            "confidence": classification.confidence,
        }
        results.append(row)
        if verbose:
            print(f"{row['symbol']:<8} id={security_id:<5} {row['type']:<13} {row['confidence']}")

    conn.execute(
        "UPDATE pipeline_runs SET status = 'success', finished_at = ?, records_written = ? "
        "WHERE run_id = ?",
        (utc_now_iso(), len(results), run_id),
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover and load the S1 universe candidate pool")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--pool-version", default=DEFAULT_POOL_VERSION)
    parser.add_argument("--target-size", type=int, default=DEFAULT_TARGET_SIZE)
    args = parser.parse_args(argv)

    load_dotenv_into_environ()
    load_config()  # validated as a side effect; fails loudly on a bad config

    sec = SecClient()
    nasdaq = NasdaqClient()
    tickers = sec.fetch_company_tickers()
    candidates = discover_candidates(nasdaq, tickers, target_size=args.target_size)
    print(f"discovered {len(candidates)} candidates for pool_version={args.pool_version!r}\n")

    conn = migrate.connect(Path(args.db))
    try:
        conn.execute("BEGIN")
        results = load_pool(conn, candidates, args.pool_version, sec)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print(f"\nloaded {len(results)} candidates into universe_candidate_pool")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
