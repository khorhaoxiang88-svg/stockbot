"""Ingest 8-K / 8-K-A filings and their exhibit documents for the fixture.

Reuses sec.acceptance.build_filing_index (migration 004's accepted_at
resolution machinery -- submissions.recent plus the paginated filings.files)
for the accession list and acceptance timestamps, filtered to form_type in
('8-K', '8-K/A'). Documents (primary + exhibits) are discovered per-accession
via EDGAR's plain index.json
(https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json,
confirmed live against a real Apple accession 2026-08-08) and every one is
stored through the existing content-hash payload store, same as every other
SEC document in this project.

Zero score influence: this script never writes to any table but news_filings,
news_filing_documents and raw_payloads (the last already generic, owned by
migration 004).
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from sec.acceptance import build_filing_index  # noqa: E402
from sec.payload_store import store_payload, utc_now  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ  # noqa: E402

SINCE_DEFAULT = "2025-01-01"
NEWS_FORMS = ("8-K", "8-K/A")
# Full submission .txt dumps and the filing's own index pages carry nothing
# extraction needs and would only bloat storage; XBRL/image/schema files
# carry no prose. Only .htm/.html document bodies are kept.
_SKIP_SUFFIX = (".xml", ".xsd", ".txt", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".json")
_INDEX_NAME_RE = re.compile(r"-index(-headers)?\.html?$", re.I)
# EDGAR's inline-XBRL viewer auto-generates one R<n>.htm per tagged fact
# ("R1.htm", "R2.htm", ...) -- a rendered data table, not filing prose. Left
# in, these sort before real exhibit filenames (uppercase 'R' < lowercase
# 'e') and would win extract.py's MAX_EXHIBITS slots ahead of the actual
# press-release exhibit. Confirmed against a real Caterpillar 8-K
# (0000018230-26-000040) alongside its genuine ex99.1/ex99.2 exhibits.
_XBRL_VIEWER_RE = re.compile(r"^R\d+\.html?$", re.I)


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


def _index_documents(sec: SecClient, cik: str, accession: str) -> list[str]:
    """Every .htm/.html document name under this accession, primary and
    exhibits alike, excluding the filing's own index pages."""
    acc_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/index.json"
    data = sec._get(url).json()
    names = []
    for item in data.get("directory", {}).get("item", []):
        name = item.get("name", "")
        if not name.lower().endswith((".htm", ".html")):
            continue
        if any(name.lower().endswith(suffix) for suffix in _SKIP_SUFFIX):
            continue
        if _INDEX_NAME_RE.search(name) or _XBRL_VIEWER_RE.match(name):
            continue
        names.append(name)
    return names


def ingest_filing(conn, sec: SecClient, security_id: int, cik: str, meta, stats: dict) -> int:
    """One 8-K/8-K-A accession: index its documents, store each payload,
    write news_filings + news_filing_documents. Returns documents written."""
    existing = conn.execute(
        "SELECT 1 FROM news_filings WHERE accession_no = ?", (meta.accession_no,)
    ).fetchone()
    if existing:
        stats["already_ingested"] += 1
        return 0

    cik10 = str(cik).zfill(10)
    try:
        document_names = _index_documents(sec, cik10, meta.accession_no)
    except Exception as exc:  # noqa: BLE001
        stats["errors"] = stats.get("errors", [])
        stats["errors"].append(f"{meta.accession_no} index.json: {exc}")
        return 0

    primary_name = meta.primary_doc_url.rsplit("/", 1)[-1] if meta.primary_doc_url else None
    if primary_name and primary_name not in document_names:
        document_names.append(primary_name)

    written_docs = 0
    primary_payload_id = None
    doc_rows: list[tuple] = []
    for name in document_names:
        doc_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/"
            f"{meta.accession_no.replace('-', '')}/{name}"
        )
        try:
            raw = sec._get(doc_url).content
        except Exception:  # noqa: BLE001
            stats["doc_fetch_failed"] += 1
            continue
        payload_id, _ = store_payload(
            conn, raw, "sec", "8-K", f"CIK{cik10}/{meta.accession_no}/{name}"
        )
        role = "primary" if name == primary_name else "exhibit"
        if role == "primary":
            primary_payload_id = payload_id
        doc_rows.append((meta.accession_no, name, role, None, payload_id))
        written_docs += 1

    if written_docs == 0:
        stats["no_documents"] += 1
        return 0

    # A primary document must exist for a filing to be usable at all; if EDGAR
    # never named one, the first stored document stands in rather than the
    # filing being silently dropped.
    if primary_payload_id is None:
        primary_payload_id = doc_rows[0][4]

    conn.execute(
        """
        INSERT INTO news_filings
            (accession_no, cik, security_id, form_type, filed_date, accepted_at,
             period_of_report, primary_doc_url, payload_id, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            meta.accession_no, cik10, security_id, meta.form_type, meta.filed_date,
            meta.accepted_at, meta.period_of_report, meta.primary_doc_url,
            primary_payload_id, utc_now(),
        ),
    )
    conn.executemany(
        """
        INSERT INTO news_filing_documents
            (accession_no, document_name, role, exhibit_label, payload_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        doc_rows,
    )
    stats["filings_written"] += 1
    stats["documents_written"] += written_docs
    if meta.accepted_at:
        stats["with_accepted_at"] += 1
    else:
        stats["without_accepted_at"] += 1
    return written_docs


def ingest_security(conn, sec: SecClient, security: dict, since: str, stats: dict) -> int:
    cik10 = str(security["cik"]).zfill(10)
    try:
        filing_index, submission_payloads = build_filing_index(sec, cik10)
    except Exception as exc:  # noqa: BLE001
        stats["errors"] = stats.get("errors", [])
        stats["errors"].append(f"{security['symbol']} ({cik10}) submissions: {exc}")
        return 0

    for raw_bytes in submission_payloads:
        store_payload(conn, raw_bytes, "sec", "submissions", f"CIK{cik10}")

    candidates = [
        meta for meta in filing_index.values()
        if meta.form_type in NEWS_FORMS and (meta.filed_date or "") >= since
    ]
    candidates.sort(key=lambda m: m.filed_date or "")

    written = 0
    for meta in candidates:
        written += ingest_filing(conn, sec, security["security_id"], cik10, meta, stats)
    print(f"{security['symbol']:<8} 8-K/8-K-A filings={len(candidates):<4} documents={written}")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest 8-K/8-K-A filings for the News Ledger")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--since", default=SINCE_DEFAULT)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    load_dotenv_into_environ()
    conn = migrate.connect(Path(args.db))
    sec = SecClient()
    stats: dict = {
        "filings_written": 0, "documents_written": 0, "already_ingested": 0,
        "no_documents": 0, "doc_fetch_failed": 0, "with_accepted_at": 0,
        "without_accepted_at": 0,
    }
    run_id = f"news-ingest-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'news_ingest', ?, 'running', '8-K')",
            (run_id, utc_now()),
        )
        securities = fixture_securities(conn)
        if args.symbols:
            wanted = {s.upper() for s in args.symbols}
            securities = [s for s in securities if s["symbol"].upper() in wanted]
        if args.limit:
            securities = securities[: args.limit]

        total = 0
        for security in securities:
            total += ingest_security(conn, sec, security, args.since, stats)

        conn.execute(
            "UPDATE pipeline_runs SET status = ?, finished_at = ?, records_written = ?, "
            "errors_json = ? WHERE run_id = ?",
            (
                "success" if not stats.get("errors") else "partial",
                utc_now(), total,
                None if not stats.get("errors") else str(stats["errors"]),
                run_id,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print("\n=== news ingest summary ===")
    print(f"filings written        : {stats['filings_written']}")
    print(f"documents written      : {stats['documents_written']}")
    print(f"already ingested       : {stats['already_ingested']}")
    print(f"accepted_at resolved   : {stats['with_accepted_at']}")
    print(f"accepted_at unresolved : {stats['without_accepted_at']}")
    if stats["no_documents"]:
        print(f"filings with no usable documents: {stats['no_documents']}")
    if stats["doc_fetch_failed"]:
        print(f"document fetch failures: {stats['doc_fetch_failed']}")
    if stats.get("errors"):
        print(f"errors: {len(stats['errors'])}")
        for message in stats["errors"][:8]:
            print(f"  {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
