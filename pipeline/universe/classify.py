"""Security classification.

Design rule from the brief: never guess from a ticker suffix alone. Every
decision must name the evidence it used, and anything unresolved becomes
"unknown" with low confidence.

Evidence ranking, strongest first:
  1. Nasdaq Trader Test Issue flag        -> test_issue
  2. Nasdaq Trader ETF flag               -> etf
  3. The official Security Name text in the directory files. These names are
     written by the exchange and say plainly what the instrument is
     ("... Warrant", "... 6.25% Series A Preferred Stock", "... Common Stock").
  4. SEC SIC code, used for industry only. It never decides instrument type.

Confidence:
  high   - an explicit flag, or an unambiguous instrument phrase in the
           official security name
  medium - a phrase that is suggestive but overlaps other instrument types
  low    - nothing decisive; type is "unknown"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Industry codes we care about naming. SIC is industry, not instrument type.
SIC_BANK_PREFIXES = ("602", "603", "6020", "6021", "6022", "6035", "6036", "6199", "6022")
SIC_REIT = "6798"


@dataclass(frozen=True)
class Classification:
    security_type: str
    confidence: str
    source: str
    share_class: str | None = None

    def __post_init__(self) -> None:
        if self.security_type == "unknown" and self.confidence == "high":
            raise ValueError("unknown classification may never be high confidence")


# Ordered rules. First match wins, so the order encodes real precedence learned
# from the live directory files:
#   * ADR must beat preferred, or "American Depositary Shares" is read as a
#     preferred share because of the word "Depositary".
#   * unit must beat warrant and right, because a SPAC unit's official name
#     describes its components ("Units, each consisting of one Class A ordinary
#     share and one-half of one redeemable warrant").
#   * trust_unit must beat unit for "Units of Beneficial Interest".
NAME_RULES: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (
        re.compile(r"\bamerican depositary (shares?|receipts?)\b", re.I),
        "adr",
        "high",
        "name:adr",
    ),
    (
        re.compile(r"\bunits? of beneficial interest\b", re.I),
        "trust_unit",
        "high",
        "name:trust_unit",
    ),
    (re.compile(r"\bunits?\b", re.I), "unit", "high", "name:unit"),
    (re.compile(r"\bwarrants?\b", re.I), "warrant", "high", "name:warrant"),
    (re.compile(r"\brights?\b", re.I), "right", "high", "name:right"),
    (
        re.compile(r"\b(preferred|depositary shares?)\b", re.I),
        "preferred_share",
        "high",
        "name:preferred",
    ),
    (
        re.compile(r"\bexchange[- ]traded note\b|\bETN\b", re.I),
        "etn",
        "high",
        "name:etn",
    ),
    (
        re.compile(r"\bclosed[- ]end fund\b", re.I),
        "closed_end_fund",
        "high",
        "name:closed_end_fund",
    ),
    (
        re.compile(r"\b(common stock|ordinary shares?|common shares?)\b", re.I),
        "common_stock",
        "high",
        "name:common",
    ),
    # "Capital Stock" is the exchange's official descriptor for common equity
    # that carries no voting rights, e.g. "Alphabet Inc. Class C Capital Stock".
    (
        re.compile(r"\bcapital stock\b", re.I),
        "common_stock",
        "high",
        "name:capital_stock",
    ),
    # Weaker: "Fund" appears in plenty of operating-company names.
    (re.compile(r"\bfund\b", re.I), "closed_end_fund", "medium", "name:fund_weak"),
)

CLASS_RE = re.compile(r"\bclass\s+([A-Z])\b", re.I)


def extract_share_class(security_name: str) -> str | None:
    """'Alphabet Inc. Class A Common Stock' -> 'A'. None when not stated."""
    match = CLASS_RE.search(security_name or "")
    return match.group(1).upper() if match else None


def classify(listing: dict[str, Any] | None) -> Classification:
    """Classify one security from its Nasdaq Trader directory record.

    `listing` is a record from NasdaqClient.fetch_all_listings(), or None when
    the symbol was not found in either directory file.
    """
    if not listing:
        return Classification("unknown", "low", "no_listing_record")

    name = listing.get("security_name") or ""
    share_class = extract_share_class(name)

    if listing.get("is_test_issue"):
        return Classification("test_issue", "high", "nasdaq:test_issue_flag", share_class)

    if listing.get("is_etf"):
        return Classification("etf", "high", "nasdaq:etf_flag", share_class)

    for pattern, security_type, confidence, label in NAME_RULES:
        if pattern.search(name):
            return Classification(security_type, confidence, f"nasdaq:{label}", share_class)

    # Some exchange names state only the share class, e.g. "Lennar Corporation
    # Class B". A listed equity with a share class and no instrument keyword is
    # common stock, but the evidence is weaker than an explicit phrase, so this
    # is medium confidence and carries its own source label.
    if share_class:
        return Classification("common_stock", "medium", "nasdaq:name_class_only", share_class)

    return Classification("unknown", "low", "nasdaq:name_unmatched", share_class)


def classify_from_security_title(title: str | None) -> Classification:
    """Classify from the SEC cover-page fact dei:Security12bTitle.

    This is the fallback for securities that are no longer in the Nasdaq Trader
    directory files, which only carry currently listed symbols. The title comes
    off the 10-K cover page ("Common Stock, $0.01 par value per share"), so it
    is filed evidence, not a guess.
    """
    if not title or not title.strip():
        return Classification("unknown", "low", "sec:security_title_missing")

    share_class = extract_share_class(title)
    for pattern, security_type, confidence, label in NAME_RULES:
        if pattern.search(title):
            return Classification(
                security_type, confidence, f"sec:dei:Security12bTitle:{label}", share_class
            )
    if share_class:
        return Classification(
            "common_stock", "medium", "sec:dei:Security12bTitle:class_only", share_class
        )
    return Classification("unknown", "low", "sec:dei:Security12bTitle:unmatched", share_class)


def industry_label(sic_code: str | None) -> str | None:
    """Human label for the industry codes the fixture cares about."""
    if not sic_code:
        return None
    code = str(sic_code).strip()
    if code == SIC_REIT:
        return "REIT"
    if code.startswith("602") or code in {"6020", "6021", "6022", "6035", "6036"}:
        return "Bank"
    return None


# Only common stock is eligible for ranking.
#
# This is a separate rule from the unknown-classification rule on purpose. A
# preferred share or a warrant can be classified perfectly, with high
# confidence, and must still never be ranked: neither is an ownership claim on
# operating earnings, so earnings-based and growth-based scores are meaningless
# for them. Excluding them as a side effect of some later model-applicability
# flag would hide the real reason.
#
# ADRs are excluded here too. An ADR does represent operating-company equity, so
# it is a candidate for inclusion later, but that needs its own decision about
# foreign-issuer reporting rather than being waved through by this set.
RANKABLE_SECURITY_TYPES: frozenset[str] = frozenset({"common_stock"})

RANKABLE_CONFIDENCES: frozenset[str] = frozenset({"high", "medium"})


def rank_exclusion_reason(security_type: str, confidence: str) -> str | None:
    """Why this security may not be ranked, or None when it may be.

    The checks are ordered so the reported reason is the real one:
      1. unresolved classification
      2. resolved, but not common stock
      3. common stock, but the classification is not trusted enough
    """
    if security_type == "unknown":
        return "classification is unknown; unknown securities are never ranked"
    if security_type not in RANKABLE_SECURITY_TYPES:
        return (
            f"security_type '{security_type}' is not common stock; "
            "only common stock is ranked"
        )
    if confidence not in RANKABLE_CONFIDENCES:
        return f"classification confidence '{confidence}' is too low to rank"
    return None


def is_rankable(security_type: str, confidence: str) -> bool:
    """The single place that decides whether a security may enter a ranking."""
    return rank_exclusion_reason(security_type, confidence) is None
