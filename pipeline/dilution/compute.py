"""Compute dilution signals for the fixture.

Two halves:

  * Share growth (D4) from dei:EntityCommonStockSharesOutstanding, SPLIT
    ADJUSTED. A 2-for-1 split doubles the share count and is not dilution, so
    the earlier count is restated onto the later basis before the ratio is taken.

  * Filing classification (D1-D3), which fetches each candidate document and
    runs it through the gates in dilution/classify.py. Nothing scores until a
    filing is established as common-equity related.

Classification is bounded per security and the cap is REPORTED, because the
fixture contains issuers with tens of thousands of 424B2 note filings. Filings
beyond the cap are recorded as unclassified and score zero, which is the
conservative direction: it can understate a diluter, never invent one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from dilution.classify import (  # noqa: E402
    SHELF_415,
    SHELF_FORMS,
    TAKEDOWN_FORMS,
    classify_filing,
    shelf_is_unexpired,
)
from dilution.score import score_from_evidence  # noqa: E402
from sec.payload_store import utc_now  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ  # noqa: E402

MAX_CLASSIFICATIONS = 40
DOC_BYTES = 60_000
TTM_DAYS = 365


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in value[:10].split("-"))
    return date(year, month, day)


# ------------------------------------------------------------------ D4 inputs


def split_factor_between(conn, security_id: int, start: str, end: str) -> float:
    """Cumulative split ratio for ex-dates in (start, end].

    Only genuine splits count. Migration 003 files spin-off adjustment factors as
    action_type 'other' with requires_manual_review, and those must not restate a
    share count.
    """
    factor = 1.0
    for row in conn.execute(
        """
        SELECT ratio FROM corporate_actions
         WHERE security_id = ? AND action_type = 'split'
           AND requires_manual_review = 0 AND ratio IS NOT NULL AND ratio > 0
           AND ex_date > ? AND ex_date <= ?
        """,
        (security_id, start, end),
    ):
        factor *= float(row["ratio"])
    return factor


def shares_yoy_growth(conn, security_id: int, cik: str, as_of_date: str):
    """Split-adjusted YoY growth in common shares outstanding.

    Returns (growth, detail) where growth is None when it cannot be computed.
    """
    rows = conn.execute(
        """
        SELECT period_end, normalized_numeric_value AS shares, accession_no, accepted_at
          FROM xbrl_facts
         WHERE cik = ? AND concept = 'EntityCommonStockSharesOutstanding'
           AND normalized_numeric_value IS NOT NULL AND period_start IS NULL
           AND period_end <= ? AND accepted_at IS NOT NULL
         ORDER BY period_end DESC
        """,
        (str(cik).zfill(10), as_of_date),
    ).fetchall()
    if not rows:
        return None, {"reason": "no EntityCommonStockSharesOutstanding facts"}

    latest = rows[0]
    target = (parse_date(latest["period_end"]) - timedelta(days=TTM_DAYS)).isoformat()

    prior = None
    for row in rows[1:]:
        if row["period_end"] <= target:
            prior = row
            break
    if prior is None:
        # Fall back to the oldest available if it is at least 9 months back.
        oldest = rows[-1]
        if (parse_date(latest["period_end"]) - parse_date(oldest["period_end"])).days >= 270:
            prior = oldest
    if prior is None:
        return None, {"reason": "no share count roughly one year earlier"}

    factor = split_factor_between(
        conn, security_id, prior["period_end"], latest["period_end"]
    )
    # Restate the earlier count onto the later split basis. Without this a
    # 2-for-1 split reads as 100% dilution.
    prior_adjusted = float(prior["shares"]) * factor
    if prior_adjusted <= 0:
        return None, {"reason": "prior share count non-positive"}

    growth = float(latest["shares"]) / prior_adjusted - 1.0
    return growth, {
        "latest_period": latest["period_end"],
        "latest_shares": float(latest["shares"]),
        "latest_accession": latest["accession_no"],
        "prior_period": prior["period_end"],
        "prior_shares_raw": float(prior["shares"]),
        "prior_shares_split_adjusted": prior_adjusted,
        "split_factor_applied": factor,
        "prior_accession": prior["accession_no"],
    }


# ------------------------------------------------------- filing classification


def candidate_filings(conn, cik: str, as_of_date: str) -> list[dict]:
    """Shelf registrations within their 3-year life, and TTM takedowns."""
    shelf_cutoff = (parse_date(as_of_date) - timedelta(days=3 * 365)).isoformat()
    ttm_cutoff = (parse_date(as_of_date) - timedelta(days=TTM_DAYS)).isoformat()

    shelves = conn.execute(
        f"""
        SELECT accession_no, form_type, filed_date, primary_doc_url
          FROM filings
         WHERE cik = ? AND form_type IN ({','.join('?' * len(SHELF_FORMS))})
           AND filed_date >= ? AND filed_date <= ?
         ORDER BY filed_date DESC
        """,
        (str(cik).zfill(10), *sorted(SHELF_FORMS), shelf_cutoff, as_of_date),
    ).fetchall()

    takedowns = conn.execute(
        f"""
        SELECT accession_no, form_type, filed_date, primary_doc_url
          FROM filings
         WHERE cik = ? AND form_type IN ({','.join('?' * len(TAKEDOWN_FORMS))})
           AND filed_date >= ? AND filed_date <= ?
         ORDER BY filed_date DESC
        """,
        (str(cik).zfill(10), *sorted(TAKEDOWN_FORMS), ttm_cutoff, as_of_date),
    ).fetchall()

    # 424B5 first: an equity takedown is far more likely there than in a 424B2,
    # which is dominated by note programmes.
    ordered = (
        [dict(r) for r in shelves]
        + [dict(r) for r in takedowns if r["form_type"] == "424B5"]
        + [dict(r) for r in takedowns if r["form_type"] != "424B5"]
    )
    return ordered


def fetch_document(sec: SecClient, url: str | None) -> str | None:
    if not url:
        return None
    try:
        sec.limiter.acquire()
        with sec.session.get(url, timeout=60, stream=True) as response:
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_content(16384):
                chunks.append(chunk)
                total += len(chunk)
                if total >= DOC_BYTES:
                    break
        text = b"".join(chunks).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text.replace("&nbsp;", " "))


def build_evidence(conn, sec: SecClient, cik: str, as_of_date: str, stats: dict) -> list[dict]:
    evidence: list[dict] = []
    candidates = candidate_filings(conn, cik, as_of_date)
    stats["candidates"] = len(candidates)

    for index, filing in enumerate(candidates):
        if index >= MAX_CLASSIFICATIONS:
            stats["skipped_by_cap"] = len(candidates) - MAX_CLASSIFICATIONS
            break
        text = fetch_document(sec, filing["primary_doc_url"])
        classification = classify_filing(filing["form_type"], text)

        item = {
            "accession": filing["accession_no"],
            "form": filing["form_type"],
            "filed_date": filing["filed_date"],
            "url": filing["primary_doc_url"],
            "outcome": classification.outcome,
            "reason": classification.reason,
            "scores": classification.scores,
            "tier": None,
        }
        if classification.outcome == SHELF_415:
            item["unexpired"] = shelf_is_unexpired(filing["filed_date"], as_of_date)
            item["tier"] = "D1" if item["unexpired"] else None
            if not item["unexpired"]:
                item["scores"] = False
                item["reason"] += "; shelf older than three years, expired"
        elif classification.scores and filing["form_type"] in TAKEDOWN_FORMS:
            item["tier"] = "D2"
        elif classification.scores:
            item["tier"] = "D3"
        evidence.append(item)

    return evidence


def compute_security(conn, sec: SecClient, security: dict, as_of_date: str) -> dict:
    stats: dict = {}
    evidence = build_evidence(conn, sec, security["cik"], as_of_date, stats)
    growth, growth_detail = shares_yoy_growth(
        conn, security["security_id"], security["cik"], as_of_date
    )
    result = score_from_evidence(evidence, growth, as_of_date)

    notes = list(result.notes)
    if stats.get("skipped_by_cap"):
        notes.append(
            f"{stats['skipped_by_cap']} of {stats['candidates']} candidate filings were not "
            f"classified (cap {MAX_CLASSIFICATIONS}); unclassified filings score zero"
        )
    notes.append(f"share growth basis: {json.dumps(growth_detail)}")

    return {
        "security_id": security["security_id"],
        "as_of_date": as_of_date,
        "shares_yoy_growth": growth,
        "d1_capacity": result.d1_capacity,
        "d2_issuance": result.d2_issuance,
        "d3_structural": result.d3_structural,
        "d4_realised": result.d4_realised,
        "dilution_score": result.total,
        "is_disqualified": 1 if result.is_disqualified else 0,
        "evidence_json": json.dumps(evidence),
        "classification_notes": " | ".join(notes),
    }


def fixture_securities(conn) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.security_id, s.cik, f.symbol_at_selection AS symbol, f.category
              FROM fixture_manifest f
              JOIN securities s ON s.security_id = f.security_id
             WHERE s.cik IS NOT NULL
             ORDER BY s.security_id
            """
        ).fetchall()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute dilution signals")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--symbols", nargs="*")
    args = parser.parse_args(argv)

    load_dotenv_into_environ()
    conn = migrate.connect(Path(args.db))
    sec = SecClient()
    run_id = f"dilution-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'dilution', ?, 'running', 'D1-D4/v1')",
            (run_id, utc_now()),
        )
        securities = fixture_securities(conn)
        if args.symbols:
            wanted = {s.upper() for s in args.symbols}
            securities = [s for s in securities if s["symbol"].upper() in wanted]

        written = 0
        for security in securities:
            row = compute_security(conn, sec, security, args.as_of)
            columns = ",".join(row)
            conn.execute(
                f"INSERT OR REPLACE INTO dilution_signals ({columns}) "
                f"VALUES ({','.join('?' * len(row))})",
                list(row.values()),
            )
            written += 1
            print(
                f"{security['symbol']:<8} D1={row['d1_capacity']:<4.0f}"
                f"D2={row['d2_issuance']:<4.0f}D3={row['d3_structural']:<4.0f}"
                f"D4={row['d4_realised']:<5.1f}total={row['dilution_score']:<5.1f}"
                f"{'DISQUALIFIED' if row['is_disqualified'] else ''}"
            )

        conn.execute(
            "UPDATE pipeline_runs SET status='success', finished_at=?, records_written=? "
            "WHERE run_id=?",
            (utc_now(), written, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print(f"\ndilution rows written: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
