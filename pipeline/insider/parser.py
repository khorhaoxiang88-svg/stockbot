"""Form 4 / 4/A XML parser.

Verified against live filings on 2026-07-30.

Document shape:

    <ownershipDocument>
      <documentType>4</documentType>            or 4/A
      <periodOfReport>2026-06-11</periodOfReport>
      <dateOfOriginalSubmission>...</...>       amendments only
      <notSubjectToSection16>0</...>
      <aff10b5One>0</aff10b5One>                Rule 10b5-1 checkbox, MODERN ONLY
      <issuer><issuerCik/><issuerTradingSymbol/></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik/><rptOwnerName/></reportingOwnerId>
        <reportingOwnerRelationship>
          <isDirector/><isOfficer/><isTenPercentOwner/><isOther/><officerTitle/>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>   Table I
        <nonDerivativeTransaction>...</nonDerivativeTransaction>
      <derivativeTable>      Table II
        <derivativeTransaction>...</derivativeTransaction>
      <footnotes><footnote id="F1">...</footnote></footnotes>

Two facts that drive the plan-status rules:

  * `aff10b5One` was present in all 14 modern filings sampled and ABSENT from a
    2018 amendment. The checkbox is a recent addition, so its absence is the
    normal state for older filings, not an error.
  * An amendment carries `dateOfOriginalSubmission` but NOT the original's
    accession number, so the link back has to be derived from
    (issuer, reporting owner, period of report) plus that date.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

TABLE_I, TABLE_II = "I", "II"

PLAN_DISCRETIONARY = "discretionary"
PLAN_CONFIRMED = "confirmed_10b5_1"
PLAN_UNKNOWN = "unknown"

SOURCE_CHECKBOX = "checkbox"
SOURCE_FOOTNOTE = "footnote"
SOURCE_ABSENT = "absent"

# "not made pursuant to a Rule 10b5-1 plan" must not be read as confirmation.
_NEGATED_10B5 = re.compile(
    r"\b(not|no|without|outside(?:\s+of)?|other\s+than)\b[^.]{0,80}?10b5[-\s]?1", re.I
)
_MENTIONS_10B5 = re.compile(r"10b5[-\s]?1", re.I)


@dataclass
class InsiderRow:
    line_no: int
    table_type: str
    transaction_code: str | None
    transaction_date: str | None
    shares: float | None
    price_per_share: float | None
    total_value: float | None
    shares_owned_after: float | None
    security_title: str | None = None


@dataclass
class Form4:
    accession_no: str | None
    document_type: str
    is_amendment: bool
    period_of_report: str | None
    date_of_original_submission: str | None
    issuer_cik: str | None
    issuer_symbol: str | None
    insider_cik: str | None
    insider_name: str | None
    role_officer: bool
    role_director: bool
    role_ten_percent: bool
    officer_title: str | None
    plan_status: str
    plan_status_source: str
    rows: list[InsiderRow] = field(default_factory=list)


def _text(node, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None:
        return None
    value = (found.text or "").strip()
    return value or None


def _value(node, path: str) -> str | None:
    """Form 4 wraps most values in <x><value>..</value></x>."""
    if node is None:
        return None
    element = node.find(path)
    if element is None:
        return None
    inner = element.find("value")
    if inner is not None:
        text = (inner.text or "").strip()
        return text or None
    text = (element.text or "").strip()
    return text or None


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _flag(raw: str | None) -> bool:
    return str(raw).strip().lower() in {"1", "true", "y", "yes"}


def determine_plan_status(root: ET.Element, footnote_text: str) -> tuple[str, str]:
    """Plan status and where it came from.

    Order: the checkbox is authoritative when the element exists. Otherwise the
    footnotes are read. When neither settles it, the answer is 'unknown' with
    source 'absent' - never a default of discretionary or confirmed.
    """
    checkbox = root.find(".//aff10b5One")
    if checkbox is not None:
        raw = (checkbox.text or "").strip()
        if raw != "":
            return (
                (PLAN_CONFIRMED, SOURCE_CHECKBOX) if _flag(raw)
                else (PLAN_DISCRETIONARY, SOURCE_CHECKBOX)
            )

    if footnote_text:
        if _NEGATED_10B5.search(footnote_text):
            return PLAN_DISCRETIONARY, SOURCE_FOOTNOTE
        if _MENTIONS_10B5.search(footnote_text):
            return PLAN_CONFIRMED, SOURCE_FOOTNOTE

    return PLAN_UNKNOWN, SOURCE_ABSENT


def _collect_footnotes(root: ET.Element) -> str:
    parts = []
    for footnote in root.iter("footnote"):
        parts.append(re.sub(r"\s+", " ", "".join(footnote.itertext())).strip())
    remarks = _text(root, "remarks")
    if remarks:
        parts.append(remarks)
    return " ".join(parts)


def _parse_transaction(node, line_no: int, table_type: str) -> InsiderRow:
    shares = _number(_value(node, "transactionAmounts/transactionShares"))
    price = _number(_value(node, "transactionAmounts/transactionPricePerShare"))
    total = shares * price if (shares is not None and price is not None) else None
    return InsiderRow(
        line_no=line_no,
        table_type=table_type,
        transaction_code=_value(node, "transactionCoding/transactionCode"),
        transaction_date=_value(node, "transactionDate"),
        shares=shares,
        price_per_share=price,
        total_value=total,
        shares_owned_after=_number(
            _value(node, "postTransactionAmounts/sharesOwnedFollowingTransaction")
        ),
        security_title=_value(node, "securityTitle"),
    )


def parse_form4(xml_text: str, accession_no: str | None = None) -> Form4:
    """Parse one Form 4 or 4/A document."""
    match = re.search(r"<ownershipDocument>.*?</ownershipDocument>", xml_text, re.S)
    if match:
        xml_text = match.group(0)
    root = ET.fromstring(xml_text)

    document_type = _text(root, "documentType") or "4"
    relationship = root.find(".//reportingOwnerRelationship")
    footnote_text = _collect_footnotes(root)
    plan_status, plan_source = determine_plan_status(root, footnote_text)

    form = Form4(
        accession_no=accession_no,
        document_type=document_type,
        is_amendment=document_type.strip().upper().endswith("/A"),
        period_of_report=_text(root, "periodOfReport"),
        date_of_original_submission=_text(root, "dateOfOriginalSubmission"),
        issuer_cik=_text(root, "issuer/issuerCik"),
        issuer_symbol=_text(root, "issuer/issuerTradingSymbol"),
        insider_cik=_text(root, ".//reportingOwnerId/rptOwnerCik"),
        insider_name=_text(root, ".//reportingOwnerId/rptOwnerName"),
        role_officer=_flag(_text(relationship, "isOfficer")),
        role_director=_flag(_text(relationship, "isDirector")),
        role_ten_percent=_flag(_text(relationship, "isTenPercentOwner")),
        officer_title=_text(relationship, "officerTitle"),
        plan_status=plan_status,
        plan_status_source=plan_source,
    )

    line_no = 0
    # Table I first, then Table II. line_no is unique within the filing and the
    # two tables are never merged.
    for node in root.iter("nonDerivativeTransaction"):
        line_no += 1
        form.rows.append(_parse_transaction(node, line_no, TABLE_I))
    for node in root.iter("derivativeTransaction"):
        line_no += 1
        form.rows.append(_parse_transaction(node, line_no, TABLE_II))
    return form
