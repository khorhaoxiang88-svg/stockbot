"""The deterministic, offline risk detectors.

Every function here takes already-resolved inputs and returns either a Flag or
an Unknown. None of them fetch anything, so all of them are testable without a
network. The going-concern detector is the one exception and lives in its own
module because it has to read filing text.

The rule that shapes the whole file: a detector that cannot run returns an
UNKNOWN, never a negative result. "We checked and it is fine" and "we could not
check" are different statements and the panel shows them in different sections.
"""

from __future__ import annotations

from dataclasses import dataclass

# The thresholds the F9 brief states as literals. Only high_leverage is
# configured, because only high_leverage was specified as configurable.
LOW_INTEREST_COVERAGE = 1.5
RAPID_SHARE_GROWTH = 0.20
REVERSE_SPLIT_LOOKBACK_DAYS = 3 * 365
INSIDER_SELLING_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Flag:
    flag_code: str
    severity: str
    evidence_text: str
    source_accession: str | None
    is_unknown: bool = False

    def as_row(self, security_id: int, as_of_date: str) -> dict:
        return {
            "security_id": security_id,
            "as_of_date": as_of_date,
            "flag_code": self.flag_code,
            "severity": self.severity,
            "evidence_text": self.evidence_text,
            "source_accession": self.source_accession,
            "is_unknown": 1 if self.is_unknown else 0,
        }


def unknown(flag_code: str, why: str) -> Flag:
    """A check that could not be performed. Never a clean result."""
    return Flag(flag_code, "unknown", f"Could not determine: {why}", None, True)


def money(value: float) -> str:
    """Human-scale money, so evidence text reads like a sentence."""
    magnitude = abs(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= cutoff:
            return f"{value / cutoff:,.2f}{suffix}"
    return f"{value:,.0f}"


# ------------------------------------------------------------------ cash flow


def negative_operating_cash_flow(cfo, period_end: str | None) -> Flag:
    if not cfo.present:
        return unknown(
            "negative_operating_cash_flow",
            "cash flow from operations was not reported for the latest complete "
            "fiscal year at the knowledge cutoff",
        )
    if cfo.value >= 0:
        return Flag(
            "negative_operating_cash_flow", "none",
            f"Not detected. Operating cash flow for {period_end} was "
            f"{money(cfo.value)}, which is positive.",
            cfo.accession,
        )
    return Flag(
        "negative_operating_cash_flow", "high",
        f"Operating cash flow for {period_end} was {money(cfo.value)}. The "
        f"business consumed cash from operations over the full fiscal year.",
        cfo.accession,
    )


def negative_free_cash_flow(cfo, capex, period_end: str | None) -> Flag:
    if not cfo.present:
        return unknown("negative_free_cash_flow", "cash flow from operations not reported")
    if not capex.present:
        return unknown(
            "negative_free_cash_flow",
            "capital expenditure not reported, so free cash flow cannot be computed; "
            "it is never assumed to be zero",
        )
    # Capex arrives as a positive outflow in the cash-flow statement.
    fcf = cfo.value - abs(capex.value)
    if fcf >= 0:
        return Flag(
            "negative_free_cash_flow", "none",
            f"Not detected. Free cash flow for {period_end} was {money(fcf)} "
            f"(operating cash flow {money(cfo.value)} less capital expenditure "
            f"{money(abs(capex.value))}).",
            cfo.accession,
        )
    return Flag(
        "negative_free_cash_flow", "medium" if cfo.value >= 0 else "high",
        f"Free cash flow for {period_end} was {money(fcf)}: operating cash flow "
        f"{money(cfo.value)} less capital expenditure {money(abs(capex.value))}.",
        cfo.accession,
    )


# ------------------------------------------------------------------- leverage


def high_leverage(debt_ebitda, accession: str | None, threshold: float,
                  period_end: str | None) -> Flag:
    if debt_ebitda is None:
        return unknown(
            "high_leverage",
            "debt/EBITDA is not available. F5 leaves it NULL when EBITDA is zero "
            "or negative, because the ratio has no interpretation there",
        )
    if debt_ebitda <= threshold:
        return Flag(
            "high_leverage", "none",
            f"Not detected. Debt/EBITDA for {period_end} was {debt_ebitda:.2f}x, "
            f"at or below the configured threshold of {threshold:.1f}x.",
            accession,
        )
    severity = "high" if debt_ebitda > threshold * 1.5 else "medium"
    return Flag(
        "high_leverage", severity,
        f"Debt/EBITDA for {period_end} was {debt_ebitda:.2f}x, above the "
        f"configured threshold of {threshold:.1f}x.",
        accession,
    )


def low_interest_coverage(coverage, accession: str | None, cap: float,
                          period_end: str | None) -> Flag:
    if coverage is None:
        return unknown(
            "low_interest_coverage",
            "interest coverage is not available. F5 leaves it NULL when debt is "
            "present but interest expense was not reported",
        )
    if coverage >= cap:
        return Flag(
            "low_interest_coverage", "none",
            f"Not detected. Interest coverage for {period_end} reached the F5 cap "
            f"of {cap:.0f}x, which is how a debt-free balance sheet is recorded.",
            accession,
        )
    if coverage >= LOW_INTEREST_COVERAGE:
        return Flag(
            "low_interest_coverage", "none",
            f"Not detected. Interest coverage for {period_end} was "
            f"{coverage:.2f}x, at or above {LOW_INTEREST_COVERAGE}x.",
            accession,
        )
    severity = "high" if coverage < 1.0 else "medium"
    return Flag(
        "low_interest_coverage", severity,
        f"Interest coverage for {period_end} was {coverage:.2f}x, below "
        f"{LOW_INTEREST_COVERAGE}x."
        + (" Operating income did not cover interest expense." if coverage < 1.0 else ""),
        accession,
    )


# --------------------------------------------------------------------- shares


def rapid_share_growth(growth: float | None, accession: str | None,
                       detail: dict | None) -> Flag:
    if growth is None:
        reason = (detail or {}).get("reason", "no split-adjusted share count comparison")
        return unknown("rapid_share_growth", reason)
    if accession is None:
        return unknown(
            "rapid_share_growth",
            f"share growth computed at {growth:.1%} but the reporting accession "
            f"could not be identified, so the number cannot be sourced",
        )
    basis = ""
    if detail:
        basis = (
            f" Comparison: {detail.get('prior_period')} "
            f"{detail.get('prior_shares_raw'):,.0f} shares restated to "
            f"{detail.get('prior_shares_split_adjusted'):,.0f} on the later split "
            f"basis (factor {detail.get('split_factor_applied')}), against "
            f"{detail.get('latest_period')} {detail.get('latest_shares'):,.0f}."
        ) if detail.get("prior_shares_raw") is not None else ""

    if growth <= RAPID_SHARE_GROWTH:
        return Flag(
            "rapid_share_growth", "none",
            f"Not detected. Split-adjusted shares outstanding grew {growth:.1%} "
            f"year over year, at or below {RAPID_SHARE_GROWTH:.0%}.{basis}",
            accession,
        )
    severity = "high" if growth > 0.50 else "medium"
    return Flag(
        "rapid_share_growth", severity,
        f"Split-adjusted shares outstanding grew {growth:.1%} year over year, "
        f"above {RAPID_SHARE_GROWTH:.0%}.{basis}",
        accession,
    )


def recent_reverse_split(action: dict | None, security_id: int,
                         checked_from: str) -> Flag:
    """A reverse split is a share consolidation: ratio below 1.

    The source here is the corporate-action ledger, not an SEC filing, because
    the price vendor is where the action was observed. The reference is written
    as 'ledger:corporate_actions:...' so it is obvious it is not an accession,
    and it resolves to exactly one row.
    """
    if action is None:
        return Flag(
            "recent_reverse_split", "none",
            f"Not detected. No split with a ratio below 1.0 is recorded on or "
            f"after {checked_from}.",
            f"ledger:corporate_actions:{security_id}:none",
        )
    ratio = float(action["ratio"])
    return Flag(
        "recent_reverse_split", "medium",
        f"Reverse split with ex-date {action['ex_date']}, ratio {ratio} "
        f"(1-for-{1 / ratio:.0f}), recorded by {action['provider']}. A reverse "
        f"split raises the share price without changing the business, and is "
        f"often used to hold a listing standard.",
        f"ledger:corporate_actions:{security_id}:{action['ex_date']}",
    )


# -------------------------------------------------------------------- filings


def dilution_flags(evidence: list[dict], notes: str | None) -> list[Flag]:
    """shelf_capacity, active_issuance and atm_or_convertible, from F7.

    F7 already classified every candidate filing and recorded why. This does not
    re-classify anything; it surfaces the classification and its reason against
    the accession that produced it.
    """
    flags: list[Flag] = []
    if not evidence:
        for code in ("shelf_capacity", "active_issuance", "atm_or_convertible"):
            flags.append(unknown(
                code,
                "no dilution evidence is recorded for this security, so no filing "
                "was classified",
            ))
        return flags

    def first(predicate):
        return next((item for item in evidence if predicate(item)), None)

    shelf = first(lambda i: i.get("tier") == "D1")
    if shelf:
        flags.append(Flag(
            "shelf_capacity", "medium",
            f"Unexpired shelf registration: {shelf['form']} filed "
            f"{shelf['filed_date']}. Classified {shelf['outcome']} because "
            f"{shelf['reason']}. A shelf is capacity to issue, not issuance.",
            shelf["accession"],
        ))
    else:
        flags.append(Flag(
            "shelf_capacity", "none",
            f"Not detected. {len(evidence)} candidate filings were classified and "
            f"none is an unexpired shelf registration.",
            evidence[0]["accession"],
        ))

    takedowns = [i for i in evidence if i.get("tier") == "D2"]
    if takedowns:
        newest = takedowns[0]
        flags.append(Flag(
            "active_issuance", "high" if len(takedowns) >= 3 else "medium",
            f"{len(takedowns)} qualifying takedown(s) in the trailing twelve "
            f"months. Most recent: {newest['form']} filed {newest['filed_date']}, "
            f"classified {newest['outcome']} because {newest['reason']}.",
            newest["accession"],
        ))
    else:
        flags.append(Flag(
            "active_issuance", "none",
            f"Not detected. No filing among the {len(evidence)} classified was a "
            f"qualifying equity takedown in the trailing twelve months.",
            evidence[0]["accession"],
        ))

    structural = first(
        lambda i: i.get("outcome") in ("atm_programme", "variable_convertible")
        and i.get("scores")
    )
    if structural:
        variable = structural["outcome"] == "variable_convertible"
        flags.append(Flag(
            "atm_or_convertible", "high" if variable else "medium",
            f"{'Variable-priced convertible' if variable else 'At-the-market programme'}"
            f" identified in {structural['form']} filed {structural['filed_date']}. "
            f"Classified {structural['outcome']} because {structural['reason']}.",
            structural["accession"],
        ))
    else:
        flags.append(Flag(
            "atm_or_convertible", "none",
            f"Not detected. None of the {len(evidence)} classified filings is an "
            f"at-the-market programme or a variable-priced convertible.",
            evidence[0]["accession"],
        ))

    if notes and "not classified" in notes:
        # The F7 cap is real and must not read as full coverage.
        flags.append(unknown(
            "stale_or_incomplete_data",
            f"dilution classification was capped: {notes.split(' | ')[0]}",
        ))
    return flags


# --------------------------------------------------------------------- insider


def recent_insider_selling(sales: list[dict], coverage_complete: bool,
                           coverage_reason: str) -> Flag:
    """CONTEXT ONLY. This is never assigned a bearish severity.

    Insiders sell to pay the tax on a vest, to diversify, to buy a house, to
    fund a divorce. A sale carries far less information than a purchase, and
    presenting one as a risk would be an interpretation with no evidence behind
    it. The database enforces this too: the CHECK on risk_flags refuses any
    severity other than 'context' or 'unknown' for this flag.
    """
    if not coverage_complete:
        return unknown("recent_insider_selling", coverage_reason)
    if not sales:
        return Flag(
            "recent_insider_selling", "context",
            f"No Table I open-market sales in the last "
            f"{INSIDER_SELLING_WINDOW_DAYS} days. Recorded as context, not as a "
            f"positive signal.",
            "none",
        )
    insiders = sorted({s.get("insider_name") or s.get("insider_cik") or "?" for s in sales})
    total = sum(float(s["total_value"]) for s in sales if s.get("total_value") is not None)
    newest = sales[0]
    return Flag(
        "recent_insider_selling", "context",
        f"{len(sales)} Table I sale(s) by {len(insiders)} insider(s) in the last "
        f"{INSIDER_SELLING_WINDOW_DAYS} days, {money(total)} in reported value. "
        f"Most recent {newest['transaction_date']} by {newest.get('insider_name')}. "
        f"CONTEXT ONLY: insiders sell for taxes, diversification and personal "
        f"reasons, and a sale is not treated as bearish anywhere in this system.",
        newest["accession_no"],
    )


# ----------------------------------------------------------- data completeness


def stale_or_incomplete_data(problems: list[dict], accession: str | None) -> Flag:
    """Names the missing fields AND the source they should have come from."""
    if not problems:
        return Flag(
            "stale_or_incomplete_data", "none",
            f"Not detected. Every source is within its freshness SLA and no "
            "required field is missing for the period scored.",
            accession or "none",
        )
    parts = [
        f"{problem['source']}: {problem['detail']}" for problem in problems
    ]
    severity = "high" if any(p.get("blocking") for p in problems) else "medium"
    return Flag(
        "stale_or_incomplete_data", severity,
        "; ".join(parts),
        accession or "none",
    )
