"""Normalising SEC Company Facts into xbrl_facts rows.

Verified against the live endpoint and the EDGAR APIs page on 2026-07-29.

Company Facts shape:

    {"cik": 320193, "entityName": "...",
     "facts": {"us-gaap": {"Revenues": {"label": ..., "description": ...,
                           "units": {"USD": [{start, end, val, accn, fy, fp,
                                              form, filed, frame}, ...]}}}}}

Every key an entry can carry was enumerated from a real payload:
    accn, end, filed, form, fp, frame, fy, start, val

Note what is NOT there: no `decimals`, no nil flag, no dimensional members.
Company Facts returns consolidated facts only. decimals, is_nil and
dimensions_json are therefore stored as NULL and source_endpoint is
'companyfacts'. They are not inferred, defaulted, or fabricated - a later phase
that parses instance documents can add them as new rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterator

SOURCE_ENDPOINT = "companyfacts"


@dataclass(frozen=True)
class FactRecord:
    source_fact_key: str
    cik: str
    taxonomy: str
    concept: str
    unit: str | None
    context_type: str
    period_start: str | None
    period_end: str | None
    dimensions_json: str | None
    context_hash: str
    semantic_hash: str
    frame: str | None
    raw_value: str | None
    normalized_numeric_value: float | None
    decimals: int | None
    is_nil: int | None
    fiscal_year: int | None
    fiscal_period: str | None
    form_type: str | None
    accession_no: str | None
    filed_date: str | None
    source_endpoint: str = SOURCE_ENDPOINT


def _canonical(payload: dict[str, Any]) -> str:
    """Deterministic JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_context_hash(
    context_type: str,
    period_start: str | None,
    period_end: str | None,
    dimensions_json: str | None,
) -> str:
    """Groups facts that share a reporting context."""
    return hashlib.sha256(
        _canonical(
            {
                "context_type": context_type,
                "period_start": period_start,
                "period_end": period_end,
                "dimensions_json": dimensions_json,
            }
        ).encode("utf-8")
    ).hexdigest()


def compute_semantic_hash(
    taxonomy: str,
    concept: str,
    unit: str | None,
    context_type: str,
    period_start: str | None,
    period_end: str | None,
    dimensions_json: str | None,
) -> str:
    """Identifies facts that MEAN the same thing.

    Used for normalisation and duplicate detection only. It is deliberately not
    a uniqueness constraint: a restatement produces two facts with the same
    semantic hash and different values, and both must survive.
    """
    return hashlib.sha256(
        _canonical(
            {
                "taxonomy": taxonomy,
                "concept": concept,
                "unit": unit,
                "context_type": context_type,
                "period_start": period_start,
                "period_end": period_end,
                "dimensions_json": dimensions_json,
            }
        ).encode("utf-8")
    ).hexdigest()


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def iter_facts(payload: dict[str, Any]) -> Iterator[FactRecord]:
    """Yield one FactRecord per entry in a Company Facts payload.

    source_fact_key is `taxonomy|concept|unit|index`, the index being the
    fact's position in its source array. That is the source document's own
    identity for the fact, so two entries that normalise identically still get
    distinct keys and are both stored.
    """
    cik = str(payload.get("cik", "")).zfill(10)
    facts = payload.get("facts") or {}

    for taxonomy, concepts in facts.items():
        for concept, body in (concepts or {}).items():
            for unit, entries in ((body or {}).get("units") or {}).items():
                for index, entry in enumerate(entries or []):
                    period_start = entry.get("start")
                    period_end = entry.get("end")
                    context_type = "duration" if period_start else "instant"
                    # Company Facts carries no dimensional members at all.
                    dimensions_json = None

                    value = entry.get("val")
                    yield FactRecord(
                        source_fact_key=f"{taxonomy}|{concept}|{unit}|{index}",
                        cik=cik,
                        taxonomy=taxonomy,
                        concept=concept,
                        unit=unit,
                        context_type=context_type,
                        period_start=period_start,
                        period_end=period_end,
                        dimensions_json=dimensions_json,
                        context_hash=compute_context_hash(
                            context_type, period_start, period_end, dimensions_json
                        ),
                        semantic_hash=compute_semantic_hash(
                            taxonomy, concept, unit, context_type,
                            period_start, period_end, dimensions_json,
                        ),
                        frame=entry.get("frame"),
                        raw_value=None if value is None else str(value),
                        normalized_numeric_value=_to_number(value),
                        # Not available from this endpoint. Never fabricated.
                        decimals=None,
                        is_nil=None,
                        fiscal_year=entry.get("fy"),
                        fiscal_period=entry.get("fp"),
                        form_type=entry.get("form"),
                        accession_no=entry.get("accn"),
                        filed_date=entry.get("filed"),
                        source_endpoint=SOURCE_ENDPOINT,
                    )
