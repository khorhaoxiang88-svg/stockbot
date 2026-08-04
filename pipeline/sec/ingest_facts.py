"""Ingest SEC Company Facts for the fixture, preserving every raw payload.

Order of operations per company:
  1. fetch submissions (all pages) -> preserve payloads -> build accession index
  2. fetch companyfacts -> preserve payload
  3. if that exact payload was already ingested, stop: facts already exist
  4. normalise every fact, attach accepted_at via its accession, INSERT

Nothing is ever updated. A fact that changes upstream arrives as a new row from
a new payload, which is exactly how a restatement is preserved.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from sec.acceptance import build_filing_index  # noqa: E402
from sec.facts import iter_facts  # noqa: E402
from sec.payload_store import store_payload, utc_now  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ  # noqa: E402

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
BATCH = 5000


@dataclass
class FactsReport:
    companies_attempted: int = 0
    companies_with_facts: int = 0
    companies_without_facts: list[str] = field(default_factory=list)
    payloads_stored: int = 0
    payloads_reused: int = 0
    payload_bytes: int = 0
    facts_written: int = 0
    facts_skipped_existing: int = 0
    filings_written: int = 0
    facts_with_accepted_at: int = 0
    facts_without_accepted_at: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def acceptance_rate(self) -> float:
        total = self.facts_with_accepted_at + self.facts_without_accepted_at
        return (self.facts_with_accepted_at / total) if total else 0.0


def fixture_ciks(conn) -> list[tuple[str, str]]:
    """(cik, label) for every distinct fixture CIK, in fixture order."""
    rows = conn.execute(
        """
        SELECT s.cik, MIN(f.symbol_at_selection) AS symbol
          FROM fixture_manifest f
          JOIN securities s ON s.security_id = f.security_id
         WHERE s.cik IS NOT NULL
         GROUP BY s.cik
         ORDER BY MIN(f.security_id)
        """
    ).fetchall()
    return [(row["cik"], row["symbol"]) for row in rows]


def ingest_company(conn, sec: SecClient, cik: str, label: str, report: FactsReport) -> None:
    cik10 = str(cik).zfill(10)

    # 1. Filing metadata first, so acceptance times are available for the facts.
    try:
        filing_index, submission_payloads = build_filing_index(sec, cik10)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"{label} ({cik10}) submissions: {exc}")
        filing_index, submission_payloads = {}, []

    for raw_bytes in submission_payloads:
        _, is_new = store_payload(conn, raw_bytes, "sec", "submissions", f"CIK{cik10}")
        report.payload_bytes += len(raw_bytes)
        if is_new:
            report.payloads_stored += 1
        else:
            report.payloads_reused += 1

    # 2. Company Facts.
    try:
        response = sec._get(COMPANYFACTS_URL.format(cik=cik10))
    except Exception as exc:  # noqa: BLE001
        report.companies_without_facts.append(label)
        report.errors.append(f"{label} ({cik10}) companyfacts: {exc}")
        return

    raw_bytes = response.content
    payload_id, is_new = store_payload(conn, raw_bytes, "sec", "companyfacts", f"CIK{cik10}")
    report.payload_bytes += len(raw_bytes)
    if is_new:
        report.payloads_stored += 1
    else:
        report.payloads_reused += 1

    # 3. Already ingested? Facts for this exact payload are already present.
    existing = conn.execute(
        "SELECT COUNT(*) FROM xbrl_facts WHERE payload_id = ?", (payload_id,)
    ).fetchone()[0]
    if existing:
        report.facts_skipped_existing += existing
        report.companies_with_facts += 1
        print(f"{label:<8} payload unchanged, {existing:,} facts already stored")
        return

    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        report.errors.append(f"{label}: companyfacts is not valid JSON: {exc}")
        return

    # 4. Filings referenced by the facts.
    written_filings = 0
    for accession, meta in filing_index.items():
        conn.execute(
            """
            INSERT INTO filings (accession_no, cik, form_type, filed_date, accepted_at,
                                 period_of_report, primary_doc_url, payload_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (accession_no) DO UPDATE SET
                accepted_at = COALESCE(filings.accepted_at, excluded.accepted_at),
                period_of_report = COALESCE(filings.period_of_report, excluded.period_of_report),
                primary_doc_url = COALESCE(filings.primary_doc_url, excluded.primary_doc_url)
            """,
            (
                accession, meta.cik, meta.form_type, meta.filed_date, meta.accepted_at,
                meta.period_of_report, meta.primary_doc_url, payload_id,
            ),
        )
        written_filings += 1
    report.filings_written += written_filings

    # 5. Facts. Append only, never upsert.
    rows: list[tuple] = []
    with_acceptance = without_acceptance = 0
    for fact in iter_facts(payload):
        meta = filing_index.get(fact.accession_no or "")
        accepted_at = meta.accepted_at if meta else None
        if accepted_at:
            with_acceptance += 1
        else:
            without_acceptance += 1
        rows.append(
            (
                payload_id, fact.source_fact_key, fact.cik, fact.taxonomy, fact.concept,
                fact.unit, fact.context_type, fact.period_start, fact.period_end,
                fact.dimensions_json, fact.context_hash, fact.semantic_hash, fact.frame,
                fact.raw_value, fact.normalized_numeric_value, fact.decimals, fact.is_nil,
                fact.fiscal_year, fact.fiscal_period, fact.form_type, fact.accession_no,
                fact.filed_date, accepted_at, fact.source_endpoint,
            )
        )
        if len(rows) >= BATCH:
            _insert(conn, rows)
            rows.clear()
    if rows:
        _insert(conn, rows)

    total = with_acceptance + without_acceptance
    report.facts_written += total
    report.facts_with_accepted_at += with_acceptance
    report.facts_without_accepted_at += without_acceptance
    report.companies_with_facts += 1
    rate = (with_acceptance / total * 100) if total else 0.0
    print(
        f"{label:<8} facts={total:<7,} accepted_at={rate:5.1f}%  "
        f"filings={written_filings:<5} payload={len(raw_bytes) / 1e6:.1f}MB"
    )


def _insert(conn, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO xbrl_facts
            (payload_id, source_fact_key, cik, taxonomy, concept, unit, context_type,
             period_start, period_end, dimensions_json, context_hash, semantic_hash, frame,
             raw_value, normalized_numeric_value, decimals, is_nil, fiscal_year,
             fiscal_period, form_type, accession_no, filed_date, accepted_at, source_endpoint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (payload_id, source_fact_key) DO NOTHING
        """,
        rows,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest SEC Company Facts")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--ciks", nargs="*", help="limit to these CIKs")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--pool",
        default=None,
        help="ingest a universe_candidate_pool version instead of the Phase F fixture "
        "(e.g. s1-sample-v1); does not touch fixture_manifest",
    )
    args = parser.parse_args(argv)

    load_dotenv_into_environ()
    conn = migrate.connect(Path(args.db))
    sec = SecClient()
    report = FactsReport()
    run_id = f"facts-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'sec_facts', ?, 'running', 'companyfacts')",
            (run_id, utc_now()),
        )
        if args.pool:
            from universe.pool import pool_securities

            seen_ciks: set[str] = set()
            companies = []
            for row in pool_securities(conn, args.pool):
                if row["cik"] in seen_ciks:
                    continue
                seen_ciks.add(row["cik"])
                companies.append((row["cik"], row["symbol"]))
        else:
            companies = fixture_ciks(conn)
        if args.ciks:
            wanted = {c.zfill(10) for c in args.ciks}
            companies = [(c, l) for c, l in companies if c.zfill(10) in wanted]
        if args.limit:
            companies = companies[: args.limit]

        report.companies_attempted = len(companies)
        for cik, label in companies:
            ingest_company(conn, sec, cik, label, report)

        conn.execute(
            "UPDATE pipeline_runs SET status = ?, finished_at = ?, records_written = ?, "
            "errors_json = ? WHERE run_id = ?",
            (
                "success" if not report.errors else "partial",
                utc_now(),
                report.facts_written,
                json.dumps(report.errors) if report.errors else None,
                run_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO source_health (source_name, last_success, consecutive_failures, staleness_hours)
            VALUES ('sec_companyfacts', ?, 0, 0)
            ON CONFLICT (source_name) DO UPDATE
               SET last_success = excluded.last_success, consecutive_failures = 0
            """,
            (utc_now(),),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print("\n=== SEC facts summary ===")
    print(f"companies attempted   : {report.companies_attempted}")
    print(f"companies with facts  : {report.companies_with_facts}")
    print(f"payloads stored       : {report.payloads_stored} (reused {report.payloads_reused})")
    print(f"payload bytes         : {report.payload_bytes:,} uncompressed")
    print(f"filings written       : {report.filings_written:,}")
    print(f"facts written         : {report.facts_written:,}")
    print(f"facts already present : {report.facts_skipped_existing:,}")
    print(f"accepted_at resolved  : {report.acceptance_rate * 100:.2f}%")
    print(f"unusable (no accepted): {report.facts_without_accepted_at:,}")
    if report.errors:
        print(f"errors                : {len(report.errors)}")
        for message in report.errors[:8]:
            print(f"  {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
