"""Reconstructing the price series as it stood at an earlier dataset version.

The prices table only holds current values, but price_revisions holds the
complete old and new OHLCV for every change, plus the dataset version either
side of it. Walking those backwards rebuilds any earlier state exactly.

This is what makes a backtest reproducible: a result computed at dataset
version 4 can be re-derived later, even after the vendor has revised the data
underneath it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class HistoricalBar:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    revision: int
    price_data_version: int


def price_state_as_of(
    conn: sqlite3.Connection, security_id: int, dataset_version: int
) -> list[HistoricalBar]:
    """The security's bars exactly as they were at `dataset_version`."""
    bars: dict[str, HistoricalBar] = {}
    for row in conn.execute(
        "SELECT date, open, high, low, close, volume, revision, price_data_version "
        "FROM prices WHERE security_id = ? ORDER BY date",
        (security_id,),
    ):
        bars[row["date"]] = HistoricalBar(
            date=row["date"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            revision=int(row["revision"]),
            price_data_version=int(row["price_data_version"]),
        )

    # Undo every revision that landed after the target version, newest first.
    for row in conn.execute(
        """
        SELECT date, revision, old_open, old_high, old_low, old_close, old_volume,
               price_data_version_before, price_data_version_after
          FROM price_revisions
         WHERE security_id = ? AND price_data_version_after > ?
         ORDER BY date, revision DESC
        """,
        (security_id, dataset_version),
    ):
        bar = bars.get(row["date"])
        if bar is None:
            continue
        bar.open = row["old_open"]
        bar.high = row["old_high"]
        bar.low = row["old_low"]
        bar.close = row["old_close"]
        bar.volume = row["old_volume"]
        bar.revision = int(row["revision"]) - 1
        bar.price_data_version = int(row["price_data_version_before"])

    # Drop rows that had not been ingested yet at the target version.
    return [
        bar
        for bar in sorted(bars.values(), key=lambda b: b.date)
        if bar.price_data_version <= dataset_version
    ]


def revisions_for_security(conn: sqlite3.Connection, security_id: int) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM price_revisions WHERE security_id = ? ORDER BY date DESC, revision DESC",
            (security_id,),
        )
    ]
