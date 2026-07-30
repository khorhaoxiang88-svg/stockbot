"""The frozen dilution formula.

  D1 Capacity   (0-4)   unexpired qualifying shelf on file          -> 4
  D2 Issuance   (0-10)  qualifying 424B2/424B5 in trailing 12 months
                        1 -> 4, 2 -> 7, 3 or more -> 10
  D3 Structural (0-8)   ATM evidence -> 4, variable convertible -> 8
                        NOT additive: take the maximum
  D4 Realised   (0-12)  g = split-adjusted YoY growth in shares outstanding
                        D4 = 12 * clamp((g - 0.05) / 0.35, 0, 1)

  dilution_score  = min(30, D1 + D2 + D3 + D4)
  is_disqualified = dilution_score >= 22

D1 + D3 cannot exceed 12, so 22 is unreachable from capacity alone.
Disqualification always requires real issuance or real share growth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dilution.classify import ATM_PROGRAMME, SHELF_415, VARIABLE_CONVERTIBLE

D4_FLOOR = 0.05   # growth below this is not treated as dilution
D4_RANGE = 0.35   # growth of floor + range earns the full 12
DISQUALIFY_AT = 22.0
MAX_SCORE = 30.0


@dataclass
class DilutionScore:
    d1_capacity: float = 0.0
    d2_issuance: float = 0.0
    d3_structural: float = 0.0
    d4_realised: float = 0.0
    shares_yoy_growth: float | None = None
    evidence: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return min(
            MAX_SCORE,
            self.d1_capacity + self.d2_issuance + self.d3_structural + self.d4_realised,
        )

    @property
    def is_disqualified(self) -> bool:
        return self.total >= DISQUALIFY_AT


def d1_capacity(has_unexpired_qualifying_shelf: bool) -> float:
    return 4.0 if has_unexpired_qualifying_shelf else 0.0


def d2_issuance(qualifying_takedowns: int) -> float:
    if qualifying_takedowns >= 3:
        return 10.0
    if qualifying_takedowns == 2:
        return 7.0
    if qualifying_takedowns == 1:
        return 4.0
    return 0.0


def d3_structural(has_atm: bool, has_variable_convertible: bool) -> float:
    """Maximum, not sum: a company with both is not doubly penalised."""
    return max(8.0 if has_variable_convertible else 0.0, 4.0 if has_atm else 0.0)


def d4_realised(shares_yoy_growth: float | None) -> float:
    """Zero when growth is unknown. Absence of data is not evidence of dilution."""
    if shares_yoy_growth is None:
        return 0.0
    scaled = (shares_yoy_growth - D4_FLOOR) / D4_RANGE
    return 12.0 * max(0.0, min(1.0, scaled))


def score_from_evidence(
    evidence: list[dict], shares_yoy_growth: float | None, as_of_date: str
) -> DilutionScore:
    """Apply the formula to already-classified, already-gated evidence."""
    scoring = [item for item in evidence if item.get("scores")]

    has_shelf = any(
        item["outcome"] == SHELF_415 and item.get("unexpired") for item in scoring
    )
    takedowns = sum(1 for item in scoring if item.get("tier") == "D2")
    has_atm = any(item["outcome"] == ATM_PROGRAMME for item in scoring)
    has_variable = any(item["outcome"] == VARIABLE_CONVERTIBLE for item in scoring)

    result = DilutionScore(
        d1_capacity=d1_capacity(has_shelf),
        d2_issuance=d2_issuance(takedowns),
        d3_structural=d3_structural(has_atm, has_variable),
        d4_realised=d4_realised(shares_yoy_growth),
        shares_yoy_growth=shares_yoy_growth,
        evidence=evidence,
    )

    unknowns = sum(1 for item in evidence if item["outcome"] == "unknown")
    debt = sum(1 for item in evidence if item["outcome"] == "debt_or_structured")
    if unknowns:
        result.notes.append(
            f"{unknowns} filings classified unknown and scored zero; "
            "unknown is absence of evidence, not risk"
        )
    if debt:
        result.notes.append(f"{debt} debt or structured-note filings scored zero equity points")
    if shares_yoy_growth is None:
        result.notes.append("share growth unavailable, D4 scored zero rather than assumed")
    return result
