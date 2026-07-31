"""Piotroski F-score assembly and the Quality gate.

The F-score is the one part of the composite that is NOT a percentile. It is an
absolute count of nine binary accounting tests, so it is contributed as
F / 9 * 100 and its 0.40 share of Quality never moves. Percentiling it would
destroy the property that makes it useful: an F-score of 8 means eight tests
passed, in any market, in any year, against no one else's numbers.

The nine signals were computed in F5 against the two most recent COMPLETE fiscal
years, with the prior year taken from annual 10-K durations rather than from any
fact date -- cover-page instants silently null every year-over-year comparison.
They are read here, never recomputed, so the composite and the fundamentals page
can never disagree about what a company's F-score is.

Gate: all nine signals present, plus at least 3 of the remaining 4 metrics. A
partial F-score is not a smaller F-score; a company where three tests could not
be evaluated has an unknown F-score, and scoring it as 6/9 would read as a
failure of the three tests that were never run.
"""

from __future__ import annotations

from dataclasses import dataclass

# Column stem -> the test as Piotroski states it, for the explanation.
PIOTROSKI_SIGNALS: tuple[tuple[str, str], ...] = (
    ("piotroski_roa_positive", "ROA > 0"),
    ("piotroski_cfo_positive", "CFO > 0"),
    ("piotroski_roa_improved", "change in ROA > 0"),
    ("piotroski_accruals", "CFO > net income"),
    ("piotroski_leverage_decreased", "change in long-term debt / assets < 0"),
    ("piotroski_current_ratio_improved", "change in current ratio > 0"),
    ("piotroski_no_new_shares", "no increase in shares outstanding"),
    ("piotroski_gross_margin_improved", "change in gross margin > 0"),
    ("piotroski_asset_turnover_improved", "change in asset turnover > 0"),
)

PIOTROSKI_WEIGHT = 0.40
# The remaining 0.60, renormalised only among themselves.
NON_PIOTROSKI_WEIGHTS: dict[str, float] = {
    "roic": 0.20,
    "interest_coverage": 0.15,
    "debt_ebitda": 0.15,
    "current_ratio": 0.10,
}
NON_PIOTROSKI_SHARE = round(sum(NON_PIOTROSKI_WEIGHTS.values()), 10)
assert abs(PIOTROSKI_WEIGHT + NON_PIOTROSKI_SHARE - 1.0) < 1e-12

MIN_VALID_NON_PIOTROSKI = 3


@dataclass(frozen=True)
class Piotroski:
    complete: bool
    f_score: int | None
    signals: list[dict]
    period_end: str | None
    prior_period_end: str | None
    reason: str | None

    @property
    def contribution_value(self) -> float | None:
        """F / 9 * 100. Absolute, never percentile-ranked."""
        if self.f_score is None:
            return None
        return self.f_score / 9.0 * 100.0

    def to_json(self) -> dict:
        return {
            "complete": self.complete,
            "f_score": self.f_score,
            "max_f_score": 9,
            "value_used": self.contribution_value,
            "formula": "F / 9 * 100, absolute (never percentile-ranked)",
            "period_end": self.period_end,
            "prior_period_end": self.prior_period_end,
            "reason": self.reason,
            "signals": self.signals,
        }


def read_piotroski(row, prior_period_end: str | None) -> Piotroski:
    """Read the nine stored signals for one fundamentals row."""
    signals, missing, total = [], [], 0
    for column, description in PIOTROSKI_SIGNALS:
        raw = row[column] if column in row.keys() else None
        passed = None if raw is None else bool(int(raw))
        signals.append(
            {
                "signal": column.replace("piotroski_", ""),
                "test": description,
                "passed": passed,
                "points": None if passed is None else int(passed),
                "concept_used": (
                    row[f"{column}_concept_used"]
                    if f"{column}_concept_used" in row.keys() else None
                ),
                "accession": (
                    row[f"{column}_accession"]
                    if f"{column}_accession" in row.keys() else None
                ),
            }
        )
        if passed is None:
            missing.append(column.replace("piotroski_", ""))
        elif passed:
            total += 1

    period_end = row["period_end"] if "period_end" in row.keys() else None
    if missing:
        return Piotroski(
            False, None, signals, period_end, prior_period_end,
            "F-score not fully computable; signals missing: " + ", ".join(missing),
        )
    if prior_period_end is None:
        return Piotroski(
            False, None, signals, period_end, prior_period_end,
            "no prior complete fiscal year to compare against",
        )
    return Piotroski(True, total, signals, period_end, prior_period_end, None)
