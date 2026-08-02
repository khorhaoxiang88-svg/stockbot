"""Altman Z'' - a RISK FLAG ONLY. It is never part of the composite score.

Z'' is the four-variable revision Altman built for non-manufacturers and for
companies outside the US industrial sample the original Z-score was fitted to.
It drops the sales/assets term, which is what made the original Z punish
asset-heavy manufacturers and flatter asset-light service companies.

    Z'' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4

    X1 = working capital / total assets
    X2 = retained earnings / total assets
    X3 = EBIT / total assets
    X4 = book value of equity / total liabilities

    Z'' > 2.60   safe zone
    1.10 - 2.60  grey zone
    Z'' < 1.10   distress zone

THE APPLICABILITY CAVEAT, which must be displayed wherever Z'' is shown.
Z'' was fitted on manufacturers and emerging-market industrials. It is not
calibrated for banks, insurers or REITs, whose balance sheets carry debt as raw
material; for pre-revenue or development-stage companies, whose retained
earnings term is dominated by cumulative research spending; or for companies
whose equity is negative for reasons unrelated to distress, such as large
buyback programmes. Every one of those cases produces a low Z'' that does not
mean what the model says it means. The flag therefore carries the caveat with
it, and the score never enters the composite.

WINSORISATION. The F8 rule is that winsorisation applies only where raw
magnitudes enter arithmetic, and Z'' inputs are the third such case after the
interest coverage cap and the current ratio cap. Each ratio is clamped before it
is weighted, because one distorted denominator -- a company with almost no total
liabilities, say -- otherwise drives X4 into the hundreds and swamps the other
three terms. Clamping is RECORDED whenever it bites.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DISTRESS_THRESHOLD = 1.10
GREY_UPPER = 2.60

COEFFICIENTS = {"x1": 6.56, "x2": 3.26, "x3": 6.72, "x4": 1.05}

# Winsorisation bounds. X1-X3 are ratios to total assets and live in [-1, 1] for
# any ordinary company; +/-2 leaves room for genuine distress without letting a
# tiny asset base produce an unbounded term. X4 is bounded below by 0 because a
# negative denominator is not meaningful and above by 10 because a
# debt-free company is already at the top of the safe zone by then.
BOUNDS = {"x1": (-2.0, 2.0), "x2": (-2.0, 2.0), "x3": (-2.0, 2.0), "x4": (0.0, 10.0)}

CAVEAT = (
    "Z'' is not calibrated for every operating-company type. It was fitted on "
    "manufacturers and emerging-market industrials, and reads low for financials, "
    "REITs, pre-revenue companies and companies with negative equity from "
    "buybacks. It is a flag only and is never part of the composite score."
)


@dataclass
class AltmanResult:
    computable: bool
    z_double_prime: float | None
    zone: str | None
    terms: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    winsorised: list[str] = field(default_factory=list)
    ebit_basis: str | None = None
    source_accession: str | None = None

    @property
    def is_distress(self) -> bool:
        return self.computable and self.z_double_prime is not None and (
            self.z_double_prime < DISTRESS_THRESHOLD
        )


def _clamp(name: str, value: float, winsorised: list[str]) -> float:
    low, high = BOUNDS[name]
    if value < low:
        winsorised.append(f"{name} clamped from {value:.4f} to {low}")
        return low
    if value > high:
        winsorised.append(f"{name} clamped from {value:.4f} to {high}")
        return high
    return value


def compute(
    *,
    current_assets: float | None,
    current_liabilities: float | None,
    retained_earnings: float | None,
    operating_income: float | None,
    pretax_income: float | None,
    interest_expense: float | None,
    equity: float | None,
    assets: float | None,
    liabilities: float | None,
    source_accession: str | None = None,
) -> AltmanResult:
    """Z'' from the inputs, or an explicit list of what was missing."""
    missing: list[str] = []
    if assets is None or assets <= 0:
        missing.append("total assets")
    if current_assets is None:
        missing.append("current assets")
    if current_liabilities is None:
        missing.append("current liabilities")
    if retained_earnings is None:
        missing.append("retained earnings")
    if equity is None:
        missing.append("stockholders equity")
    if liabilities is None or liabilities <= 0:
        missing.append("total liabilities")

    # EBIT: operating income when reported, otherwise rebuilt from pretax income
    # plus interest expense. Never zero-filled.
    ebit: float | None = None
    ebit_basis: str | None = None
    if operating_income is not None:
        ebit, ebit_basis = operating_income, "operating income as reported"
    elif pretax_income is not None and interest_expense is not None:
        ebit = pretax_income + interest_expense
        ebit_basis = "pretax income + interest expense"
    else:
        missing.append("EBIT (no operating income, and pretax income + interest unavailable)")

    if missing:
        return AltmanResult(False, None, None, {}, missing, [], ebit_basis, source_accession)

    winsorised: list[str] = []
    raw = {
        "x1": (current_assets - current_liabilities) / assets,
        "x2": retained_earnings / assets,
        "x3": ebit / assets,
        "x4": equity / liabilities,
    }
    used = {name: _clamp(name, value, winsorised) for name, value in raw.items()}
    z = sum(COEFFICIENTS[name] * used[name] for name in used)

    zone = (
        "distress" if z < DISTRESS_THRESHOLD
        else "grey" if z < GREY_UPPER
        else "safe"
    )
    terms = {
        name: {
            "raw": raw[name],
            "used": used[name],
            "coefficient": COEFFICIENTS[name],
            "contribution": COEFFICIENTS[name] * used[name],
        }
        for name in used
    }
    terms["inputs"] = {
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "retained_earnings": retained_earnings,
        "ebit": ebit,
        "equity": equity,
        "assets": assets,
        "liabilities": liabilities,
    }
    return AltmanResult(True, z, zone, terms, [], winsorised, ebit_basis, source_accession)
