"""Resolving acceptance timestamps.

filed_date alone cannot support an intraday cutoff: a filing accepted at
21:30 UTC is not knowable to a strategy running at 14:00 UTC the same day.
Company Facts does not carry acceptance time at all, so it is resolved through
the accession number in EDGAR submissions metadata.

`submissions.recent` holds only the most recent ~1000 filings. Company Facts
routinely references older accessions than that (AAPL: 71 fact-bearing
accessions, of which 27 fall outside the recent window), so the paginated
`filings.files` pages are fetched too. Skipping them would leave acceptance
times unresolved for a large share of historical facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"


@dataclass(frozen=True)
class FilingMeta:
    accession_no: str
    cik: str
    form_type: str | None
    filed_date: str | None
    accepted_at: str | None
    period_of_report: str | None
    primary_doc_url: str | None


def normalise_acceptance(value: str | None) -> str | None:
    """EDGAR returns '2026-06-17T22:40:43.000Z'. Store UTC to the second."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1]
    if "." in text:
        text = text.split(".", 1)[0]
    # Already UTC per EDGAR documentation; stamp it explicitly.
    return f"{text}Z" if len(text) >= 19 else None


def _primary_doc_url(cik: str, accession_no: str, document: str | None) -> str | None:
    if not document:
        return None
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession_no.replace('-', '')}/{document}"
    )


def _rows_from_block(cik: str, block: dict[str, Any]) -> list[FilingMeta]:
    accessions = block.get("accessionNumber") or []
    if not accessions:
        return []
    forms = block.get("form") or []
    filed = block.get("filingDate") or []
    accepted = block.get("acceptanceDateTime") or []
    report = block.get("reportDate") or []
    documents = block.get("primaryDocument") or []

    def at(sequence, index):
        return sequence[index] if index < len(sequence) else None

    return [
        FilingMeta(
            accession_no=accession,
            cik=cik,
            form_type=at(forms, index),
            filed_date=at(filed, index),
            accepted_at=normalise_acceptance(at(accepted, index)),
            period_of_report=at(report, index) or None,
            primary_doc_url=_primary_doc_url(cik, accession, at(documents, index)),
        )
        for index, accession in enumerate(accessions)
    ]


def build_filing_index(sec_client, cik: str) -> tuple[dict[str, FilingMeta], list[bytes]]:
    """Every filing EDGAR knows about for this CIK, keyed by accession.

    Returns (index, raw_payloads) so the caller can preserve the raw bytes of
    every page it used.
    """
    cik10 = str(cik).zfill(10)
    raw = sec_client._get(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    payloads = [raw.content]
    submissions = raw.json()

    index: dict[str, FilingMeta] = {}
    for meta in _rows_from_block(cik10, submissions.get("filings", {}).get("recent", {})):
        index[meta.accession_no] = meta

    for page in submissions.get("filings", {}).get("files", []) or []:
        name = page.get("name")
        if not name:
            continue
        try:
            page_raw = sec_client._get(SUBMISSIONS_PAGE_URL.format(name=name))
        except Exception:  # noqa: BLE001
            continue
        payloads.append(page_raw.content)
        try:
            block = json.loads(page_raw.content)
        except json.JSONDecodeError:
            continue
        # Older pages are the same column-oriented block, without the wrapper.
        for meta in _rows_from_block(cik10, block):
            index.setdefault(meta.accession_no, meta)

    return index, payloads
