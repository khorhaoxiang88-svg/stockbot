"""Fetch and store Form 4 insider transactions for the fixture.

Amendments are linked after all filings are parsed: a 4/A carries
dateOfOriginalSubmission but not the original's accession, so the original is
found by (issuer, reporting owner, period of report) filed at or before the
amendment. The original row is retained and marked superseded; reads filter it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from insider.parser import parse_form4  # noqa: E402
from sec.payload_store import store_payload, utc_now  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ  # noqa: E402

SINCE_DEFAULT = "2025-01-01"
MAX_PER_SECURITY = 60
# Amendments are rare per company but not rare across 46 of them, and each one
# costs a request at the SEC's 8/s ceiling. Bounded so a full run finishes; the
# cap is reported rather than applied silently.
MAX_AMENDMENTS_PER_SECURITY = 10


def resolve_security_id(conn, issuer_symbol: str | None, issuer_cik: str | None) -> int | None:
    """Which security did this Form 4 report on?

    Resolution uses ONLY the filing's own issuer fields. Two ways this went
    wrong before:

      * Resolving by the fixture CIK alone put Arbor Realty's insider trades on
        ABR$D, the preferred share, purely because that is the only Arbor
        security in the fixture. Those are not trades in the preferred.
      * A company's EDGAR submissions feed also contains filings where it is the
        REPORTING OWNER rather than the issuer. Johnson & Johnson's feed carries
        a 4/A whose issuer is CVRx, because J&J held a stake in CVRx. Treating
        the feed's CIK as the issuer recorded a sale of CVRX stock as a JNJ
        insider sale, which is exactly the kind of error that would manufacture
        a fake edge.

    Order: the filing's issuerTradingSymbol, then the filing's issuerCik matched
    to a common stock, then NULL. NULL means "we hold no security for this
    issuer" - the row is still stored, just not attributed to the wrong company.
    """
    if issuer_symbol:
        row = conn.execute(
            "SELECT security_id FROM listings WHERE symbol = ? AND valid_to IS NULL",
            (issuer_symbol.strip().upper(),),
        ).fetchone()
        if row:
            return int(row["security_id"])

    if not issuer_cik:
        return None
    row = conn.execute(
        "SELECT security_id FROM securities WHERE cik = ? AND security_type = 'common_stock' "
        "ORDER BY security_id LIMIT 1",
        (str(issuer_cik).zfill(10),),
    ).fetchone()
    return int(row["security_id"]) if row else None


def fixture_securities(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.security_id, s.cik, MIN(f.symbol_at_selection) AS symbol
          FROM fixture_manifest f
          JOIN securities s ON s.security_id = f.security_id
         WHERE s.cik IS NOT NULL
         GROUP BY s.cik
         ORDER BY MIN(f.security_id)
        """
    ).fetchall()
    return [dict(r) for r in rows]


def xml_url(cik: str, accession: str, document: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{re.sub(r'^xsl[^/]*/', '', document)}"
    )


def ingest_security(conn, sec: SecClient, security: dict, since: str, stats: Counter,
                    verbose: bool = True) -> int:
    cik = str(security["cik"]).zfill(10)
    try:
        subs = sec.fetch_submissions(cik)
    except Exception as exc:  # noqa: BLE001
        stats["errors"] += 1
        print(f"{security['symbol']}: submissions failed {exc}", file=sys.stderr)
        return 0

    recent = subs["filings"]["recent"]
    candidates = [
        {"form": form, "filed": filed, "accession": accession, "doc": doc,
         "accepted": accepted}
        for form, filed, accession, doc, accepted in zip(
            recent["form"], recent["filingDate"], recent["accessionNumber"],
            recent["primaryDocument"], recent.get("acceptanceDateTime", []),
        )
        if form in ("4", "4/A") and filed >= since
    ]
    # Amendments drive the supersede path, so they get their own allowance
    # rather than competing with ordinary filings for the cap.
    all_amendments = [c for c in candidates if c["form"] == "4/A"]
    all_originals = [c for c in candidates if c["form"] == "4"]
    amendments = all_amendments[:MAX_AMENDMENTS_PER_SECURITY]
    originals = all_originals[:MAX_PER_SECURITY]
    stats["dropped_by_cap"] += (len(all_amendments) - len(amendments)) + (
        len(all_originals) - len(originals)
    )
    selected = amendments + originals

    written = 0
    for filing in selected:
        try:
            response = sec._get(xml_url(cik, filing["accession"], filing["doc"]))
        except Exception:  # noqa: BLE001
            stats["fetch_failed"] += 1
            continue
        raw = response.content
        payload_id, _ = store_payload(
            conn, raw, "sec", "form4", f"CIK{cik}/{filing['accession']}"
        )
        try:
            form = parse_form4(response.text, filing["accession"])
        except Exception:  # noqa: BLE001
            stats["parse_failed"] += 1
            continue

        accepted = filing["accepted"] or None
        if accepted:
            accepted = accepted.replace(" ", "T").split(".")[0]
            if not accepted.endswith("Z"):
                accepted += "Z"

        # The filing's own issuer, never the feed we happened to find it in.
        resolved_security_id = resolve_security_id(
            conn, form.issuer_symbol, form.issuer_cik
        )
        if resolved_security_id is None:
            stats["unattributed_filings"] += 1

        for row in form.rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO insider_transactions
                    (accession_no, line_no, security_id, insider_cik, insider_name,
                     role_officer, role_director, role_ten_percent, officer_title,
                     transaction_date, filed_date, accepted_at, table_type,
                     transaction_code, plan_status, plan_status_source, shares,
                     price_per_share, total_value, shares_owned_after, is_amendment,
                     amends_accession, superseded_by_accession, payload_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    filing["accession"], row.line_no, resolved_security_id,
                    form.insider_cik, form.insider_name,
                    1 if form.role_officer else 0, 1 if form.role_director else 0,
                    1 if form.role_ten_percent else 0, form.officer_title,
                    row.transaction_date, filing["filed"], accepted, row.table_type,
                    row.transaction_code, form.plan_status, form.plan_status_source,
                    row.shares, row.price_per_share, row.total_value,
                    row.shares_owned_after, 1 if form.is_amendment else 0,
                    # NULL until link_amendments identifies the original. A 4/A
                    # does not carry the original's accession, and inventing a
                    # self-reference to satisfy a constraint was the bug that
                    # migration 007 removes.
                    None,
                    payload_id,
                ),
            )
            written += 1
            stats[f"code:{row.transaction_code}"] += 1
            stats[f"table:{row.table_type}"] += 1
            stats[f"plan:{form.plan_status}"] += 1
        stats["filings"] += 1
        if form.is_amendment:
            stats["amendments"] += 1

    if verbose:
        print(f"{security['symbol']:<8} filings={len(selected):<4} rows={written}")
    return written


def link_amendments(conn) -> int:
    """Point each amendment at what it amends and supersede those rows.

    The original is found by (security, insider, period) filed on or before the
    amendment. The original row stays; only superseded_by_accession is set.
    """
    linked = 0
    amendments = conn.execute(
        """
        SELECT DISTINCT accession_no, security_id, insider_cik, transaction_date, filed_date
          FROM insider_transactions WHERE is_amendment = 1
        """
    ).fetchall()

    for amendment in amendments:
        originals = conn.execute(
            """
            SELECT DISTINCT accession_no FROM insider_transactions
             WHERE is_amendment = 0
               AND security_id IS ?
               AND insider_cik IS ?
               AND accession_no <> ?
               AND filed_date <= ?
               AND (transaction_date IS ? OR transaction_date IS NULL)
             ORDER BY filed_date DESC
             LIMIT 1
            """,
            (
                amendment["security_id"], amendment["insider_cik"],
                amendment["accession_no"], amendment["filed_date"],
                amendment["transaction_date"],
            ),
        ).fetchall()
        for original in originals:
            conn.execute(
                "UPDATE insider_transactions SET superseded_by_accession = ? "
                "WHERE accession_no = ? AND superseded_by_accession IS NULL",
                (amendment["accession_no"], original["accession_no"]),
            )
            conn.execute(
                "UPDATE insider_transactions SET amends_accession = ? WHERE accession_no = ?",
                (original["accession_no"], amendment["accession_no"]),
            )
            linked += 1
    return linked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest Form 4 insider transactions")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--since", default=SINCE_DEFAULT)
    parser.add_argument("--symbols", nargs="*")
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
    stats: Counter = Counter()
    run_id = f"insider-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'insider', ?, 'running', 'form4')",
            (run_id, utc_now()),
        )
        if args.pool:
            from universe.pool import pool_securities

            securities = pool_securities(conn, args.pool)
        else:
            securities = fixture_securities(conn)
        if args.symbols:
            wanted = {s.upper() for s in args.symbols}
            securities = [s for s in securities if s["symbol"].upper() in wanted]

        total = 0
        for security in securities:
            total += ingest_security(conn, sec, security, args.since, stats)
        linked = link_amendments(conn)

        conn.execute(
            "UPDATE pipeline_runs SET status='success', finished_at=?, records_written=? "
            "WHERE run_id=?",
            (utc_now(), total, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print("\n=== insider ingest summary ===")
    print(f"filings parsed     : {stats['filings']}")
    print(f"amendments         : {stats['amendments']} (linked {linked})")
    print(f"rows written       : {total}")
    print("by transaction code:")
    for key, count in sorted(stats.items()):
        if key.startswith("code:"):
            print(f"  {key[5:]:<6} {count}")
    print("by table:")
    for key, count in sorted(stats.items()):
        if key.startswith("table:"):
            print(f"  Table {key[6:]:<4} {count}")
    print("plan status:")
    for key, count in sorted(stats.items()):
        if key.startswith("plan:"):
            print(f"  {key[5:]:<18} {count}")
    if stats["dropped_by_cap"]:
        print(
            f"filings skipped by cap: {stats['dropped_by_cap']} "
            f"(max {MAX_PER_SECURITY} originals and {MAX_AMENDMENTS_PER_SECURITY} "
            "amendments per security)"
        )
    if stats["fetch_failed"] or stats["parse_failed"]:
        print(f"fetch failures={stats['fetch_failed']} parse failures={stats['parse_failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
