"""Classification gates.

Nothing scores until a filing is established as relating to common-equity
issuance. The motivating fact, measured from the fixture on 2026-07-30: there
are 126,659 424B2 filings across 39 issuers, and the overwhelming majority are
bank medium-term notes and structured notes. Awarding points per 424B2 would
disqualify JPMorgan and US Bancorp for issuing debt.

Outcomes:

    equity_offering       common stock sold or registered for resale
    atm_programme         at-the-market programme
    variable_convertible  convertible whose conversion price floats with market
    shelf_415             Rule 415 shelf capacity (S-3 family)
    debt_or_structured    notes, MTN programmes, structured notes -> ZERO
    unknown               could not be established -> ZERO, and never a penalty

An 'unknown' is not risk. It is an absence of evidence, and the brief is
explicit that it must not produce automatic penalty points.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EQUITY_OFFERING = "equity_offering"
ATM_PROGRAMME = "atm_programme"
VARIABLE_CONVERTIBLE = "variable_convertible"
SHELF_415 = "shelf_415"
DEBT_OR_STRUCTURED = "debt_or_structured"
UNKNOWN = "unknown"

SHELF_FORMS = {"S-3", "S-3/A", "S-3ASR", "S-1", "S-1/A"}
TAKEDOWN_FORMS = {"424B2", "424B5"}

# A US shelf registration is good for three years.
SHELF_LIFE_DAYS = 3 * 365

_DEBT = re.compile(
    r"medium[-\s]term note|senior note|subordinated note|structured note|"
    r"\bnotes\s+due\b|principal amount of|fixed[-\s]rate note|floating[-\s]rate note|"
    r"callable (fixed|step|zero)|market[-\s]linked|contingent income|autocallable|"
    r"digital note|buffered|trigger note|certificates of deposit",
    re.I,
)
_EQUITY = re.compile(
    r"shares of (our )?common stock|ordinary shares|shares of common stock|"
    r"resale of|selling (stockholder|shareholder)s?|"
    r"offering of .{0,40}common stock|common shares",
    re.I,
)
# Deliberately strict. "at-the-market" alone is boilerplate in the Plan of
# Distribution of ordinary NOTE offerings, and matching it classified Alphabet's
# 2026 debt takedowns as an equity ATM programme. An ATM claim now requires the
# phrase to be tied to an actual programme AND to common equity.
_ATM = re.compile(
    r"at[-\s]the[-\s]market (offering|program|programme|sales|transactions?)"
    r".{0,400}?(common stock|ordinary shares)|"
    r"(common stock|ordinary shares).{0,400}?at[-\s]the[-\s]market "
    r"(offering|program|programme|sales)|"
    r"\bATM (program|programme|facility)\b|"
    r"sales agreement .{0,120}(common stock|ordinary shares)|"
    r"rule 415\(a\)\(4\)",
    re.I | re.S,
)

_VARIABLE_CONVERTIBLE = re.compile(
    r"variable conversion price|conversion price .{0,80}(discount|percentage of|lower of)|"
    r"floating conversion|conversion price shall equal .{0,60}(market|VWAP)|"
    r"death spiral|reset provision .{0,40}conversion",
    re.I,
)
_RULE_415 = re.compile(r"rule\s*415", re.I)
_CONVERTIBLE = re.compile(r"convertible (note|debenture|preferred)", re.I)


@dataclass(frozen=True)
class Classification:
    outcome: str
    reason: str
    is_equity_related: bool

    @property
    def scores(self) -> bool:
        """Whether this classification may contribute points at all."""
        return self.outcome in {
            EQUITY_OFFERING, ATM_PROGRAMME, VARIABLE_CONVERTIBLE, SHELF_415
        }


def classify_filing(form_type: str, text: str | None) -> Classification:
    """Classify one filing from its document text.

    Order matters. Debt is checked before equity because a note prospectus
    routinely mentions common stock in passing (as the reference asset of a
    structured note, or in the issuer description), and reading that as an
    equity offering is precisely the false positive that would disqualify a bank.
    """
    form = (form_type or "").upper()

    if not text or not text.strip():
        return Classification(UNKNOWN, "no document text available to classify", False)

    debt = _DEBT.search(text)
    equity = _EQUITY.search(text)
    atm = _ATM.search(text)
    variable = _VARIABLE_CONVERTIBLE.search(text)

    # DEBT IS CHECKED FIRST. A note prospectus routinely mentions common stock
    # (as a structured note's reference asset, or in boilerplate) and testing
    # equity language first let Alphabet's debt takedowns score as an ATM.
    # Only genuinely floating conversion terms override a debt classification,
    # because a variable-priced convertible really is dilutive.
    if debt:
        if variable:
            return Classification(
                VARIABLE_CONVERTIBLE,
                f"convertible with floating terms alongside note language: "
                f"{variable.group(0)[:50]!r}",
                True,
            )
        return Classification(
            DEBT_OR_STRUCTURED,
            f"debt or structured-note offering: {debt.group(0)[:60]!r}",
            False,
        )

    if variable:
        return Classification(
            VARIABLE_CONVERTIBLE,
            f"variable or floating conversion terms: {variable.group(0)[:60]!r}",
            True,
        )
    if atm:
        return Classification(
            ATM_PROGRAMME, "at-the-market programme tied to common equity", True
        )

    if form in SHELF_FORMS:
        if _RULE_415.search(text) and equity:
            return Classification(
                SHELF_415, "Rule 415 shelf registering common equity", True
            )
        if equity:
            return Classification(
                EQUITY_OFFERING, f"common-equity registration: {equity.group(0)[:60]!r}", True
            )
        return Classification(
            UNKNOWN, "registration statement with no clear common-equity evidence", False
        )

    if form in TAKEDOWN_FORMS:
        if equity:
            return Classification(
                EQUITY_OFFERING,
                f"takedown of common equity: {equity.group(0)[:60]!r}",
                True,
            )
        if _CONVERTIBLE.search(text):
            return Classification(
                UNKNOWN,
                "convertible instrument with no evidence of floating conversion terms",
                False,
            )
        return Classification(
            UNKNOWN, "prospectus supplement with no clear common-equity evidence", False
        )

    return Classification(UNKNOWN, f"form {form} not classified by these gates", False)


def shelf_is_unexpired(filed_date: str, as_of_date: str) -> bool:
    """A US shelf lasts three years from effectiveness.

    filed_date is used as the effectiveness proxy: an S-3ASR is effective on
    filing, and for a non-automatic S-3 effectiveness follows filing, so this
    can only ever make a shelf look SHORTER-lived than it is, never longer.
    """
    from datetime import date

    def parse(value: str) -> date:
        year, month, day = (int(part) for part in value[:10].split("-"))
        return date(year, month, day)

    return (parse(as_of_date) - parse(filed_date)).days <= SHELF_LIFE_DAYS
