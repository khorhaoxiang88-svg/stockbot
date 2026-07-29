"""Read-time adjustment.

Nothing in the database is adjusted. Adjusted series are computed here, on
demand, from the raw prices plus the corporate actions ledger. That means a
later correction to a split ratio changes every chart immediately and does not
require rewriting price history.

Split adjustment, the standard convention:

    factor(d) = product of ratios of every split whose ex_date is AFTER d
    adjusted_close(d) = close(d) / factor(d)
    adjusted_volume(d) = volume(d) * factor(d)

A bar on or after the ex_date already reflects the split, so its factor is 1.
Applied correctly, the adjusted series has no jump at the split: a 10-for-1
split takes a $1200 pre-split close to $120, which lines up with the $120 the
stock actually opened at afterwards.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class AdjustedBar:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    factor: float


def split_events(conn: sqlite3.Connection, security_id: int) -> list[tuple[str, float]]:
    """(ex_date, ratio) for every split, oldest first. Ignores flagged ratios."""
    rows = conn.execute(
        """
        SELECT ex_date, ratio
          FROM corporate_actions
         WHERE security_id = ? AND action_type = 'split'
           AND ratio IS NOT NULL AND ratio > 0
           AND requires_manual_review = 0
         ORDER BY ex_date
        """,
        (security_id,),
    ).fetchall()
    return [(row[0], float(row[1])) for row in rows]


def split_factor_for_date(splits: list[tuple[str, float]], date: str) -> float:
    """Cumulative ratio of every split that happens strictly after `date`."""
    factor = 1.0
    for ex_date, ratio in splits:
        if ex_date > date:
            factor *= ratio
    return factor


def adjusted_series(
    conn: sqlite3.Connection, security_id: int, include_dividends: bool = False
) -> list[AdjustedBar]:
    """Split-adjusted series. Dividend adjustment is opt-in and off by default.

    Splits and dividends answer different questions. A split adjustment makes a
    price series comparable to itself; a dividend adjustment turns it into a
    total-return series. Mixing them by default would quietly change what a
    chart means, so the caller has to ask.
    """
    splits = split_events(conn, security_id)
    dividends: list[tuple[str, float]] = []
    if include_dividends:
        dividends = [
            (row[0], float(row[1]))
            for row in conn.execute(
                """
                SELECT ex_date, cash_amount FROM corporate_actions
                 WHERE security_id = ? AND action_type = 'dividend'
                   AND cash_amount IS NOT NULL AND requires_manual_review = 0
                 ORDER BY ex_date
                """,
                (security_id,),
            )
        ]

    rows = conn.execute(
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE security_id = ? ORDER BY date",
        (security_id,),
    ).fetchall()

    bars: list[AdjustedBar] = []
    for row in rows:
        date = row["date"]
        factor = split_factor_for_date(splits, date)

        dividend_factor = 1.0
        if include_dividends and row["close"]:
            for ex_date, amount in dividends:
                if ex_date > date:
                    # Standard back-adjustment: scale by (1 - D/P) at the ex-date.
                    close = float(row["close"]) / factor
                    if close > 0:
                        dividend_factor *= max(1e-9, 1.0 - (amount / factor) / close)

        def scale(value):
            if value is None:
                return None
            return round(float(value) / factor * dividend_factor, 6)

        bars.append(
            AdjustedBar(
                date=date,
                open=scale(row["open"]),
                high=scale(row["high"]),
                low=scale(row["low"]),
                close=scale(row["close"]),
                volume=None if row["volume"] is None else int(round(int(row["volume"]) * factor)),
                factor=factor,
            )
        )
    return bars


def largest_single_day_return(bars: list[AdjustedBar]) -> tuple[float, str | None]:
    """Biggest absolute one-day move in an adjusted series.

    Used to prove a split introduced no artificial gap: after correct
    adjustment, the return across the ex-date should look like a normal
    trading day, not like a 90% collapse.
    """
    worst = 0.0
    worst_date = None
    previous = None
    for bar in bars:
        if previous and previous.close and bar.close:
            change = abs(bar.close / previous.close - 1.0)
            if change > worst:
                worst = change
                worst_date = bar.date
        previous = bar
    return worst, worst_date


def return_across_date(bars: list[AdjustedBar], date: str) -> float | None:
    """One-day return across a specific date, using the prior bar as the base."""
    previous = None
    for bar in bars:
        if bar.date == date:
            if previous and previous.close and bar.close:
                return bar.close / previous.close - 1.0
            return None
        previous = bar
    return None
