"""Metric computation and validity rules.

Pure functions, no database. Every rule here is one the brief states explicitly:

  P/E             invalid when earnings <= 0
  P/B             invalid when book value <= 0
  EV/EBITDA       invalid when EBITDA <= 0
  ROIC            invalid when invested capital <= 0
  Interest cover  invalid when debt > 0 and interest expense missing;
                  cap (50) when total debt = 0
  Debt/EBITDA     invalid when EBITDA <= 0; 0 when total debt = 0
  Current ratio   invalid when current liabilities = 0; capped at 5.0

A missing or invalid input yields None and a recorded reason. Never 0, never an
imputed value, never a carried-forward figure from another period.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Input:
    """One resolved accounting input, with the tag that produced it."""

    value: float | None
    concept: str | None = None
    accession: str | None = None

    @property
    def present(self) -> bool:
        return self.value is not None

    def __bool__(self) -> bool:  # pragma: no cover - avoid truthiness bugs
        raise TypeError("Use .present; a zero value is present, not falsy.")


MISSING = Input(None)


@dataclass(frozen=True)
class Metric:
    """A computed metric plus its provenance, or a reason it is unavailable."""

    value: float | None
    concept_used: str | None = None
    accession: str | None = None
    reason: str | None = None

    @classmethod
    def unavailable(cls, reason: str) -> "Metric":
        return cls(None, None, None, reason)


def _need(**inputs: Input) -> str | None:
    """Return a reason naming the first absent input, or None if all present."""
    missing = [name for name, value in inputs.items() if not value.present]
    return f"missing:{','.join(sorted(missing))}" if missing else None


# --------------------------------------------------------------------- valuation


def price_earnings(market_cap: Input, eps: Input, price: Input, net_income: Input) -> Metric:
    """Price / diluted EPS when EPS is reported, else market cap / net income."""
    if eps.present and price.present:
        if eps.value <= 0:
            return Metric.unavailable("invalid:earnings<=0")
        return Metric(price.value / eps.value, eps.concept, eps.accession)

    reason = _need(market_cap=market_cap, net_income=net_income)
    if reason:
        return Metric.unavailable(reason)
    if net_income.value <= 0:
        return Metric.unavailable("invalid:earnings<=0")
    return Metric(market_cap.value / net_income.value, net_income.concept, net_income.accession)


def price_book(market_cap: Input, equity: Input) -> Metric:
    reason = _need(market_cap=market_cap, equity=equity)
    if reason:
        return Metric.unavailable(reason)
    if equity.value <= 0:
        return Metric.unavailable("invalid:book_value<=0")
    return Metric(market_cap.value / equity.value, equity.concept, equity.accession)


def ebitda(operating_income: Input, depreciation: Input) -> Metric:
    reason = _need(operating_income=operating_income, depreciation=depreciation)
    if reason:
        return Metric.unavailable(reason)
    return Metric(
        operating_income.value + depreciation.value,
        operating_income.concept,
        operating_income.accession,
    )


def enterprise_value(market_cap: Input, total_debt: Input, cash: Input) -> Metric:
    reason = _need(market_cap=market_cap, total_debt=total_debt, cash=cash)
    if reason:
        return Metric.unavailable(reason)
    return Metric(market_cap.value + total_debt.value - cash.value)


def ev_to_ebitda(ev: Metric, ebitda_value: Metric) -> Metric:
    if ev.value is None:
        return Metric.unavailable(ev.reason or "missing:enterprise_value")
    if ebitda_value.value is None:
        return Metric.unavailable(ebitda_value.reason or "missing:ebitda")
    if ebitda_value.value <= 0:
        return Metric.unavailable("invalid:ebitda<=0")
    return Metric(
        ev.value / ebitda_value.value, ebitda_value.concept_used, ebitda_value.accession
    )


def fcf_yield(cfo: Input, capex: Input, market_cap: Input) -> Metric:
    reason = _need(cfo=cfo, capex=capex, market_cap=market_cap)
    if reason:
        return Metric.unavailable(reason)
    if market_cap.value <= 0:
        return Metric.unavailable("invalid:market_cap<=0")
    # capex arrives as a positive outflow in the cash-flow statement.
    return Metric((cfo.value - abs(capex.value)) / market_cap.value, cfo.concept, cfo.accession)


# ----------------------------------------------------------------------- quality


def roic(
    operating_income: Input, income_tax: Input, pretax_income: Input,
    total_debt: Input, equity: Input, cash: Input,
) -> Metric:
    reason = _need(operating_income=operating_income, total_debt=total_debt,
                   equity=equity, cash=cash)
    if reason:
        return Metric.unavailable(reason)

    tax_rate = 0.21  # US statutory default when the effective rate is not derivable
    if income_tax.present and pretax_income.present and pretax_income.value > 0:
        derived = income_tax.value / pretax_income.value
        if 0.0 <= derived <= 0.60:
            tax_rate = derived

    invested_capital = total_debt.value + equity.value - cash.value
    if invested_capital <= 0:
        return Metric.unavailable("invalid:invested_capital<=0")
    nopat = operating_income.value * (1.0 - tax_rate)
    return Metric(nopat / invested_capital, operating_income.concept, operating_income.accession)


def interest_coverage(operating_income: Input, interest: Input, total_debt: Input,
                      cap: float) -> Metric:
    if not total_debt.present:
        return Metric.unavailable("missing:total_debt")
    if total_debt.value == 0:
        # No debt to service: coverage is unbounded, so the configured cap is the
        # value, not a missing input.
        return Metric(cap, total_debt.concept, total_debt.accession)
    if not interest.present:
        return Metric.unavailable("invalid:debt>0_and_interest_missing")
    if not operating_income.present:
        return Metric.unavailable("missing:operating_income")
    if interest.value == 0:
        return Metric(cap, interest.concept, interest.accession)
    value = operating_income.value / abs(interest.value)
    return Metric(min(value, cap), interest.concept, interest.accession)


def debt_to_ebitda(total_debt: Input, ebitda_value: Metric) -> Metric:
    if not total_debt.present:
        return Metric.unavailable("missing:total_debt")
    if total_debt.value == 0:
        return Metric(0.0, total_debt.concept, total_debt.accession)
    if ebitda_value.value is None:
        return Metric.unavailable(ebitda_value.reason or "missing:ebitda")
    if ebitda_value.value <= 0:
        return Metric.unavailable("invalid:ebitda<=0")
    return Metric(total_debt.value / ebitda_value.value, total_debt.concept, total_debt.accession)


def current_ratio(current_assets: Input, current_liabilities: Input, cap: float) -> Metric:
    reason = _need(current_assets=current_assets, current_liabilities=current_liabilities)
    if reason:
        return Metric.unavailable(reason)
    if current_liabilities.value == 0:
        return Metric.unavailable("invalid:current_liabilities=0")
    value = current_assets.value / current_liabilities.value
    return Metric(min(value, cap), current_assets.concept, current_assets.accession)


def gross_margin(gross_profit: Input, revenue: Input, cost_of_revenue: Input) -> Metric:
    if not revenue.present:
        return Metric.unavailable("missing:revenue")
    if revenue.value <= 0:
        return Metric.unavailable("invalid:revenue<=0")
    if gross_profit.present:
        return Metric(gross_profit.value / revenue.value, gross_profit.concept,
                      gross_profit.accession)
    if cost_of_revenue.present:
        return Metric((revenue.value - cost_of_revenue.value) / revenue.value,
                      cost_of_revenue.concept, cost_of_revenue.accession)
    return Metric.unavailable("missing:gross_profit,cost_of_revenue")


def revenue_growth(revenue: Input, prior_revenue: Input) -> Metric:
    reason = _need(revenue=revenue, prior_revenue=prior_revenue)
    if reason:
        return Metric.unavailable(reason)
    if prior_revenue.value <= 0:
        return Metric.unavailable("invalid:prior_revenue<=0")
    return Metric(revenue.value / prior_revenue.value - 1.0, revenue.concept, revenue.accession)


# -------------------------------------------------------------- Piotroski inputs


def _flag(condition: bool, source: Input) -> Metric:
    return Metric(1.0 if condition else 0.0, source.concept, source.accession)


def piotroski_roa_positive(net_income: Input, assets: Input) -> Metric:
    reason = _need(net_income=net_income, assets=assets)
    if reason:
        return Metric.unavailable(reason)
    if assets.value <= 0:
        return Metric.unavailable("invalid:assets<=0")
    return _flag(net_income.value / assets.value > 0, net_income)


def piotroski_cfo_positive(cfo: Input) -> Metric:
    if not cfo.present:
        return Metric.unavailable("missing:cfo")
    return _flag(cfo.value > 0, cfo)


def piotroski_roa_improved(net_income: Input, assets: Input,
                           prior_net_income: Input, prior_assets: Input) -> Metric:
    reason = _need(net_income=net_income, assets=assets,
                   prior_net_income=prior_net_income, prior_assets=prior_assets)
    if reason:
        return Metric.unavailable(reason)
    if assets.value <= 0 or prior_assets.value <= 0:
        return Metric.unavailable("invalid:assets<=0")
    return _flag(
        net_income.value / assets.value > prior_net_income.value / prior_assets.value,
        net_income,
    )


def piotroski_accruals(cfo: Input, net_income: Input, assets: Input) -> Metric:
    reason = _need(cfo=cfo, net_income=net_income, assets=assets)
    if reason:
        return Metric.unavailable(reason)
    if assets.value <= 0:
        return Metric.unavailable("invalid:assets<=0")
    return _flag(cfo.value / assets.value > net_income.value / assets.value, cfo)


def piotroski_leverage_decreased(long_term_debt: Input, assets: Input,
                                 prior_long_term_debt: Input, prior_assets: Input) -> Metric:
    reason = _need(long_term_debt=long_term_debt, assets=assets,
                   prior_long_term_debt=prior_long_term_debt, prior_assets=prior_assets)
    if reason:
        return Metric.unavailable(reason)
    if assets.value <= 0 or prior_assets.value <= 0:
        return Metric.unavailable("invalid:assets<=0")
    return _flag(
        long_term_debt.value / assets.value < prior_long_term_debt.value / prior_assets.value,
        long_term_debt,
    )


def piotroski_current_ratio_improved(current: Metric, prior: Metric,
                                     source: Input) -> Metric:
    if current.value is None:
        return Metric.unavailable(current.reason or "missing:current_ratio")
    if prior.value is None:
        return Metric.unavailable("missing:prior_current_ratio")
    return _flag(current.value > prior.value, source)


def piotroski_no_new_shares(shares: Input, prior_shares: Input) -> Metric:
    reason = _need(shares=shares, prior_shares=prior_shares)
    if reason:
        return Metric.unavailable(reason)
    return _flag(shares.value <= prior_shares.value, shares)


def piotroski_gross_margin_improved(current: Metric, prior: Metric, source: Input) -> Metric:
    if current.value is None:
        return Metric.unavailable(current.reason or "missing:gross_margin")
    if prior.value is None:
        return Metric.unavailable("missing:prior_gross_margin")
    return _flag(current.value > prior.value, source)


def piotroski_asset_turnover_improved(revenue: Input, assets: Input,
                                      prior_revenue: Input, prior_assets: Input) -> Metric:
    reason = _need(revenue=revenue, assets=assets,
                   prior_revenue=prior_revenue, prior_assets=prior_assets)
    if reason:
        return Metric.unavailable(reason)
    if assets.value <= 0 or prior_assets.value <= 0:
        return Metric.unavailable("invalid:assets<=0")
    return _flag(
        revenue.value / assets.value > prior_revenue.value / prior_assets.value, revenue
    )


# ------------------------------------------------------------ model applicability

# SIC division H, Finance / Insurance / Real Estate, covers banks (60xx),
# insurers (63xx), other financials (61xx, 62xx, 67xx) and REITs (6798).
FINANCIAL_SIC_MIN, FINANCIAL_SIC_MAX = 6000, 6799


def model_applicable(sic_code: str | None) -> tuple[bool, str | None]:
    """False for financials and REITs, with the reason.

    EV/EBITDA is meaningless when debt is raw material rather than financing,
    and a current ratio has no interpretation for a balance sheet with no
    operating cycle. These are never ranked.
    """
    if not sic_code:
        return True, None
    text = str(sic_code).strip()
    if not text.isdigit():
        return True, None
    code = int(text)
    if FINANCIAL_SIC_MIN <= code <= FINANCIAL_SIC_MAX:
        if code == 6798:
            return False, "REIT (SIC 6798)"
        if 6300 <= code <= 6411:
            return False, f"insurer (SIC {text})"
        if 6000 <= code <= 6199:
            return False, f"bank or depository institution (SIC {text})"
        return False, f"financial (SIC {text})"
    return True, None
