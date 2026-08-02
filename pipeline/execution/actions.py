"""Corporate actions applied to a LIVE position, day by day.

This is deliberately separate from pipeline/prices/adjust.py. That module
recomputes an entire historical series at read time for charts; this one
mutates one open position's quantity, stop and target as an ex-date is crossed
during a hold, and it must run in ex-date order, once per event, exactly once.

Ordering rule 1 of R1-PROTOCOL-1.1: apply the action to quantity, stop and
target BEFORE that day's OHLC is evaluated. So `apply_split` and
`apply_dividend` run first for a session, and only then does
protocol.evaluate_bar look at the day's prices.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SplitResult:
    shares_after: float
    entry_price_after: float
    stop_after: float
    target_after: float
    note: str


def apply_split(*, ratio: float, shares: float, entry_price: float, stop: float,
                target: float) -> SplitResult:
    """shares * r; entry_price, stop and target each / r.

    entry_price must move with stop and target, per R1-PROTOCOL-1.1's ordering
    rule 2, or the stored cost basis and the stored risk levels end up on two
    different bases -- gross P&L (shares * (exit - entry)) would then compare a
    post-split share count against a pre-split entry price and manufacture a
    return out of the split alone. ATR is untouched: it is frozen at entry and
    already expressed on a post-split basis for every ex-date after it, since
    the source series is adjusted at read time.
    """
    if ratio <= 0:
        raise ValueError(f"split ratio must be positive, got {ratio}")
    return SplitResult(
        shares_after=shares * ratio,
        entry_price_after=entry_price / ratio,
        stop_after=stop / ratio,
        target_after=target / ratio,
        note=f"split ratio {ratio}: shares *{ratio}, entry price/stop/target /{ratio}",
    )


@dataclass(frozen=True)
class DividendResult:
    entitled: bool
    cash_accrued: float
    note: str


def apply_dividend(*, entry_date: str, ex_date: str, shares: float,
                   cash_amount: float) -> DividendResult:
    """Entitlement requires entry_date < ex_date. A position opened ON the
    ex-date receives nothing -- ordinary dividend mechanics: the ex-date is the
    first day the stock trades without the dividend attached, so a position that
    opens that day never held the entitled share.
    """
    entitled = entry_date < ex_date
    if not entitled:
        return DividendResult(
            False, 0.0,
            f"entry {entry_date} is not before ex-date {ex_date}; no entitlement",
        )
    accrued = shares * cash_amount
    return DividendResult(
        True, accrued,
        f"entry {entry_date} precedes ex-date {ex_date}; {shares:.4f} shares * "
        f"{cash_amount} = {accrued:.4f} accrued on the ex-date",
    )


# Actions that neither a split nor an ordinary cash dividend can represent.
# R1-PROTOCOL-1.1 rule 5: these FREEZE the position rather than being applied.
MANUAL_REVIEW_ACTION_TYPES = frozenset({"spinoff", "merger", "rights", "other"})
