"""S2: scaled ingestion orchestration.

No new ingest logic lives here. Every tier calls the SAME per-item function
the corresponding Phase F / S1 script already uses (ingest_securities,
ingest_security, ingest_company, compute_for_security, compute_snapshot) --
this module only adds what F3-F5/insider/membership don't have on their own:
per-item transactions (so a kill loses at most one item's work, not the whole
batch), resumability via orchestration_progress (migration 017), a
consecutive-failure circuit breaker, and a runtime/coverage summary.

Tiered refresh cadence itself (daily prices, daily Form 4, scheduled XBRL,
monthly universe) is a scheduling decision, not logic this module owns --
that's O2's job (cron / Task Scheduler). This module just runs one tier, once,
for whatever pool and batch_id the caller names; a scheduler invokes it at
whatever cadence each tier needs.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections import Counter
from datetime import date
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import load_config  # noqa: E402
from dilution.compute import (  # noqa: E402
    compute_security as dilution_compute_security,
    fixture_securities as dilution_fixture_securities,
)
from fundamentals.compute import (  # noqa: E402
    CONCEPT_MAP,
    compute_for_security,
    fixture_securities as fundamentals_fixture_securities,
    load_concept_mappings,
    seed_concept_mappings,
)
from fundamentals.mappings import MAPPING_VERSION  # noqa: E402
from insider.ingest import (  # noqa: E402
    fixture_securities as insider_fixture_securities,
    ingest_security,
    link_amendments,
)
from orchestrate.progress import batch_summary, mark_item, pending_items, utc_now_iso  # noqa: E402
from prices.ingest import fixture_securities as prices_fixture_securities  # noqa: E402
from prices.ingest import ingest_securities  # noqa: E402
from prices.registry import get_provider  # noqa: E402
from riskflags.compute import (  # noqa: E402
    compute_security as riskflags_compute_security,
    fixture_securities as riskflags_fixture_securities,
)
from sec.ingest_facts import FactsReport, fixture_ciks, ingest_company  # noqa: E402
from universe import membership as M  # noqa: E402
from universe.pool import pool_securities  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ  # noqa: E402

# Stop a tier early rather than grind through a systemic outage (SEC blocking
# the User-Agent, a network partition) item by item. "No source shows
# repeated consecutive failures" is the manual checklist's own bar; this is
# what enforces it automatically instead of relying on someone noticing.
MAX_CONSECUTIVE_FAILURES = 5

# dilution and riskflags added when auditing Phase S found neither had ever
# run against the pool: two more per-security, per-item stages with exactly
# the shape this module already exists to scale (see run_dilution_tier /
# run_riskflags_tier below).
TIERS = ("prices", "form4", "xbrl", "universe", "dilution", "riskflags")


def default_batch_id(tier: str) -> str:
    from datetime import datetime, timezone

    return f"{tier}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _start_run(conn, stage: str) -> str:
    run_id = f"orchestrate-{uuid.uuid4().hex[:12]}"
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES (?, ?, ?, 'running', 's2-orchestrator')",
        (run_id, stage, utc_now_iso()),
    )
    conn.execute("COMMIT")
    return run_id


def _finish_run(conn, run_id: str, status: str, records_written: int, errors: list[str]) -> None:
    import json

    conn.execute("BEGIN")
    conn.execute(
        "UPDATE pipeline_runs SET status = ?, finished_at = ?, records_written = ?, "
        "errors_json = ? WHERE run_id = ?",
        (status, utc_now_iso(), records_written, json.dumps(errors) if errors else None, run_id),
    )
    conn.execute("COMMIT")


def _security_list(conn, tier_name: str, pool_version: str | None, limit: int | None):
    """(security_id, cik, symbol) rows, from the pool if named, else the
    Phase F fixture. Each of the three scripts' own fixture_securities has a
    slightly different row shape; normalised here to one shape."""
    if pool_version:
        rows = pool_securities(conn, pool_version)
        result = [(r["security_id"], r["cik"], r["symbol"]) for r in rows]
    elif tier_name == "prices":
        result = [(sid, None, sym) for sid, sym in prices_fixture_securities(conn)]
    elif tier_name == "form4":
        result = [(r["security_id"], r["cik"], r["symbol"]) for r in insider_fixture_securities(conn)]
    elif tier_name == "fundamentals":
        result = [
            (r["security_id"], r["cik"], r["symbol"]) for r in fundamentals_fixture_securities(conn)
        ]
    elif tier_name == "dilution":
        result = [(r["security_id"], r["cik"], r["symbol"]) for r in dilution_fixture_securities(conn)]
    elif tier_name == "riskflags":
        result = [(r["security_id"], r["cik"], r["symbol"]) for r in riskflags_fixture_securities(conn)]
    else:
        raise ValueError(tier_name)
    if limit:
        result = result[:limit]
    return result


def run_prices_tier(conn, batch_id: str, pool_version: str | None, years: int, limit: int | None):
    run_id = _start_run(conn, "orchestrate_prices")
    provider = get_provider()
    securities = _security_list(conn, "prices", pool_version, limit)
    keys = [sym for _, _, sym in securities]
    by_key = {sym: (sid, sym) for sid, _, sym in securities}
    todo = pending_items(conn, batch_id, "prices", keys)

    written = 0
    errors: list[str] = []
    consecutive_failures = 0
    for key in todo:
        sid, sym = by_key[key]
        conn.execute("BEGIN")
        try:
            report = ingest_securities(conn, provider, [(sid, sym)], years=years, run_id=run_id,
                                        verbose=False)
            mark_item(conn, batch_id, "prices", key, "success", run_id)
            conn.execute("COMMIT")
            written += report.rows_inserted + report.revisions_detected
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            conn.execute("ROLLBACK")
            conn.execute("BEGIN")
            mark_item(conn, batch_id, "prices", key, "failed", run_id, str(exc))
            conn.execute("COMMIT")
            errors.append(f"{key}: {exc}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                errors.append(
                    f"aborting: {consecutive_failures} consecutive failures, "
                    "likely a systemic issue rather than per-security problems"
                )
                break

    summary = batch_summary(conn, batch_id, "prices")
    status = "success" if not summary["failed"] else ("partial" if summary["success"] else "failed")
    _finish_run(conn, run_id, status, written, errors)
    return {"run_id": run_id, "summary": summary, "errors": errors}


def run_form4_tier(conn, batch_id: str, pool_version: str | None, since: str, limit: int | None):
    run_id = _start_run(conn, "orchestrate_form4")
    load_dotenv_into_environ()
    sec = SecClient()
    securities = _security_list(conn, "form4", pool_version, limit)
    stats: Counter = Counter()
    keys = [sym for _, _, sym in securities]
    by_key = {sym: {"security_id": sid, "cik": cik, "symbol": sym} for sid, cik, sym in securities}
    todo = pending_items(conn, batch_id, "form4", keys)

    written = 0
    errors: list[str] = []
    consecutive_failures = 0
    for key in todo:
        security = by_key[key]
        conn.execute("BEGIN")
        try:
            count = ingest_security(conn, sec, security, since, stats, verbose=False)
            mark_item(conn, batch_id, "form4", key, "success", run_id)
            conn.execute("COMMIT")
            written += count
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            conn.execute("ROLLBACK")
            conn.execute("BEGIN")
            mark_item(conn, batch_id, "form4", key, "failed", run_id, str(exc))
            conn.execute("COMMIT")
            errors.append(f"{key}: {exc}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                errors.append(f"aborting: {consecutive_failures} consecutive failures")
                break

    if not errors:
        conn.execute("BEGIN")
        link_amendments(conn)
        conn.execute("COMMIT")

    summary = batch_summary(conn, batch_id, "form4")
    status = "success" if not summary["failed"] else ("partial" if summary["success"] else "failed")
    _finish_run(conn, run_id, status, written, errors)
    return {"run_id": run_id, "summary": summary, "errors": errors}


def run_xbrl_tier(conn, batch_id: str, pool_version: str | None, limit: int | None):
    run_id = _start_run(conn, "orchestrate_xbrl")
    load_dotenv_into_environ()
    sec = SecClient()

    if pool_version:
        seen: set[str] = set()
        ciks: list[tuple[str, str]] = []
        for row in pool_securities(conn, pool_version):
            if row["cik"] in seen:
                continue
            seen.add(row["cik"])
            ciks.append((row["cik"], row["symbol"]))
    else:
        ciks = fixture_ciks(conn)
    if limit:
        ciks = ciks[:limit]

    keys = [cik for cik, _ in ciks]
    by_key = dict(ciks)
    todo = pending_items(conn, batch_id, "xbrl", keys)

    report = FactsReport()
    errors: list[str] = []
    consecutive_failures = 0
    for cik in todo:
        label = by_key[cik]
        conn.execute("BEGIN")
        try:
            ingest_company(conn, sec, cik, label, report)
            mark_item(conn, batch_id, "xbrl", cik, "success", run_id)
            conn.execute("COMMIT")
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            conn.execute("ROLLBACK")
            conn.execute("BEGIN")
            mark_item(conn, batch_id, "xbrl", cik, "failed", run_id, str(exc))
            conn.execute("COMMIT")
            errors.append(f"{label} ({cik}): {exc}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                errors.append(f"aborting: {consecutive_failures} consecutive failures")
                break

    summary = batch_summary(conn, batch_id, "xbrl")
    status = "success" if not summary["failed"] else ("partial" if summary["success"] else "failed")
    _finish_run(conn, run_id, status, report.facts_written, errors)
    return {"run_id": run_id, "summary": summary, "errors": errors, "facts_report": report}


def run_dilution_tier(conn, batch_id: str, pool_version: str | None, as_of: str, limit: int | None):
    run_id = _start_run(conn, "orchestrate_dilution")
    load_dotenv_into_environ()
    sec = SecClient()
    securities = _security_list(conn, "dilution", pool_version, limit)
    keys = [sym for _, _, sym in securities]
    by_key = {sym: {"security_id": sid, "cik": cik, "symbol": sym} for sid, cik, sym in securities}
    todo = pending_items(conn, batch_id, "dilution", keys)

    written = 0
    errors: list[str] = []
    consecutive_failures = 0
    for key in todo:
        security = by_key[key]
        conn.execute("BEGIN")
        try:
            row = dilution_compute_security(conn, sec, security, as_of)
            columns = ",".join(row)
            conn.execute(
                f"INSERT OR REPLACE INTO dilution_signals ({columns}) "
                f"VALUES ({','.join('?' * len(row))})",
                list(row.values()),
            )
            mark_item(conn, batch_id, "dilution", key, "success", run_id)
            conn.execute("COMMIT")
            written += 1
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            conn.execute("ROLLBACK")
            conn.execute("BEGIN")
            mark_item(conn, batch_id, "dilution", key, "failed", run_id, str(exc))
            conn.execute("COMMIT")
            errors.append(f"{key}: {exc}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                errors.append(f"aborting: {consecutive_failures} consecutive failures")
                break

    summary = batch_summary(conn, batch_id, "dilution")
    status = "success" if not summary["failed"] else ("partial" if summary["success"] else "failed")
    _finish_run(conn, run_id, status, written, errors)
    return {"run_id": run_id, "summary": summary, "errors": errors}


def run_riskflags_tier(conn, batch_id: str, pool_version: str | None, as_of: str, cfg,
                        limit: int | None):
    run_id = _start_run(conn, "orchestrate_riskflags")
    load_dotenv_into_environ()
    sec = SecClient()
    cutoff = f"{as_of}T23:59:59Z"
    securities = _security_list(conn, "riskflags", pool_version, limit)
    keys = [sym for _, _, sym in securities]
    by_key = {sym: {"security_id": sid, "cik": cik, "symbol": sym} for sid, cik, sym in securities}
    todo = pending_items(conn, batch_id, "riskflags", keys)

    written = 0
    errors: list[str] = []
    consecutive_failures = 0
    for key in todo:
        security = by_key[key]
        conn.execute("BEGIN")
        try:
            rows = riskflags_compute_security(conn, sec, security, as_of, cutoff, cfg, False)
            for row in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO risk_flags (security_id, as_of_date, "
                    "flag_code, severity, evidence_text, source_accession, is_unknown) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["security_id"], row["as_of_date"], row["flag_code"],
                     row["severity"], row["evidence_text"], row["source_accession"],
                     row["is_unknown"]),
                )
            mark_item(conn, batch_id, "riskflags", key, "success", run_id)
            conn.execute("COMMIT")
            written += len(rows)
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            conn.execute("ROLLBACK")
            conn.execute("BEGIN")
            mark_item(conn, batch_id, "riskflags", key, "failed", run_id, str(exc))
            conn.execute("COMMIT")
            errors.append(f"{key}: {exc}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                errors.append(f"aborting: {consecutive_failures} consecutive failures")
                break

    summary = batch_summary(conn, batch_id, "riskflags")
    status = "success" if not summary["failed"] else ("partial" if summary["success"] else "failed")
    _finish_run(conn, run_id, status, written, errors)
    return {"run_id": run_id, "summary": summary, "errors": errors}


def run_universe_tier(conn, batch_id: str, pool_version: str | None, run_type: str):
    """One holistic computation, not a per-security loop -- treated as a
    single item ('snapshot') for progress purposes."""
    run_id = _start_run(conn, "orchestrate_universe")
    todo = pending_items(conn, batch_id, "universe", ["snapshot"])

    errors: list[str] = []
    snapshot_id = None
    if todo:
        from universe.sec_client import utc_today

        conn.execute("BEGIN")
        try:
            snapshot_id = M.compute_snapshot(
                conn, utc_today(), run_type, pool_versions=[pool_version] if pool_version else []
            )
            mark_item(conn, batch_id, "universe", "snapshot", "success", run_id)
            conn.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001
            conn.execute("ROLLBACK")
            conn.execute("BEGIN")
            mark_item(conn, batch_id, "universe", "snapshot", "failed", run_id, str(exc))
            conn.execute("COMMIT")
            errors.append(str(exc))

    summary = batch_summary(conn, batch_id, "universe")
    status = "success" if not summary["failed"] else "failed"
    _finish_run(conn, run_id, status, 1 if snapshot_id else 0, errors)
    return {"run_id": run_id, "summary": summary, "errors": errors, "snapshot_id": snapshot_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S2 scaled-ingestion orchestrator")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--tier", choices=TIERS + ("all",), required=True)
    parser.add_argument("--pool", default=None, help="universe_candidate_pool version")
    parser.add_argument("--batch-id", default=None, help="stable id; reuse to resume")
    parser.add_argument("--limit", type=int, default=None, help="cap item count, for testing")
    parser.add_argument("--years", type=int, default=3, help="prices tier only")
    parser.add_argument("--since", default="2015-01-01", help="form4 tier only")
    parser.add_argument(
        "--run-type", default="monthly_membership",
        choices=("monthly_membership", "daily_safety"), help="universe tier only",
    )
    parser.add_argument(
        "--as-of", default=date.today().isoformat(),
        help="dilution and riskflags tiers only",
    )
    args = parser.parse_args(argv)

    cfg = load_config()  # fails loudly on a bad config before any network call
    conn = migrate.connect(Path(args.db))

    tiers = TIERS if args.tier == "all" else (args.tier,)
    results = {}
    started = time.monotonic()
    try:
        for tier in tiers:
            batch_id = args.batch_id or default_batch_id(tier)
            print(f"\n=== tier: {tier}  batch_id: {batch_id} ===")
            if tier == "prices":
                results[tier] = run_prices_tier(conn, batch_id, args.pool, args.years, args.limit)
            elif tier == "form4":
                results[tier] = run_form4_tier(conn, batch_id, args.pool, args.since, args.limit)
            elif tier == "xbrl":
                results[tier] = run_xbrl_tier(conn, batch_id, args.pool, args.limit)
            elif tier == "universe":
                results[tier] = run_universe_tier(conn, batch_id, args.pool, args.run_type)
            elif tier == "dilution":
                results[tier] = run_dilution_tier(conn, batch_id, args.pool, args.as_of, args.limit)
            elif tier == "riskflags":
                results[tier] = run_riskflags_tier(
                    conn, batch_id, args.pool, args.as_of, cfg, args.limit
                )
            print(f"  summary: {results[tier]['summary']}")
            if results[tier]["errors"]:
                print(f"  errors ({len(results[tier]['errors'])}):")
                for message in results[tier]["errors"][:10]:
                    print(f"    {message}")
    finally:
        conn.close()

    elapsed = time.monotonic() - started
    print(f"\n=== orchestration runtime: {elapsed:.1f}s ({elapsed / 60:.1f} min) ===")
    any_failed = any(r["summary"]["failed"] for r in results.values())
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
