"""Internal identity: security_id is the only stable key.

A ticker symbol is a label that a security wears for a period of time. The same
symbol can be worn by one company, dropped, then worn by a completely different
company years later. Every lookup by symbol therefore needs a date, and returns
whichever security held that symbol on that date.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


class IdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ListingWindow:
    security_id: int
    symbol: str
    exchange: str
    valid_from: str
    valid_to: str | None
    is_primary: bool


def create_security(
    conn: sqlite3.Connection,
    *,
    name: str,
    classification_source: str,
    cik: str | None = None,
    share_class: str | None = None,
    security_type: str = "unknown",
    classification_confidence: str = "low",
    sic_code: str | None = None,
    first_seen: str,
    last_seen: str,
    is_active: bool = True,
    delisted_date: str | None = None,
) -> int:
    """Insert a security and return its new security_id."""
    cursor = conn.execute(
        """
        INSERT INTO securities (
            cik, share_class, name, security_type, classification_confidence,
            classification_source, sic_code, first_seen, last_seen, is_active, delisted_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cik,
            share_class,
            name,
            security_type,
            classification_confidence,
            classification_source,
            sic_code,
            first_seen,
            last_seen,
            1 if is_active else 0,
            delisted_date,
        ),
    )
    return int(cursor.lastrowid)


def add_listing(
    conn: sqlite3.Connection,
    *,
    security_id: int,
    symbol: str,
    exchange: str,
    valid_from: str,
    valid_to: str | None = None,
    is_primary: bool = True,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO listings
            (security_id, symbol, exchange, valid_from, valid_to, is_primary)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (security_id, symbol.upper(), exchange, valid_from, valid_to, 1 if is_primary else 0),
    )


def resolve_symbol(
    conn: sqlite3.Connection, symbol: str, as_of: str, primary_only: bool = False
) -> int | None:
    """Which security held `symbol` on the UTC date `as_of`?

    valid_from is inclusive, valid_to is exclusive, NULL valid_to means current.
    Returns None when no security held the symbol on that date.
    """
    clause = " AND is_primary = 1" if primary_only else ""
    rows = conn.execute(
        f"""
        SELECT security_id
          FROM listings
         WHERE symbol = ?
           AND valid_from <= ?
           AND (valid_to IS NULL OR valid_to > ?)
           {clause}
         ORDER BY is_primary DESC, valid_from DESC
        """,
        (symbol.upper(), as_of, as_of),
    ).fetchall()
    if not rows:
        return None
    return int(rows[0][0])


def symbol_history(conn: sqlite3.Connection, symbol: str) -> list[ListingWindow]:
    """Every security that has ever held this symbol, oldest window first."""
    rows = conn.execute(
        """
        SELECT security_id, symbol, exchange, valid_from, valid_to, is_primary
          FROM listings
         WHERE symbol = ?
         ORDER BY valid_from
        """,
        (symbol.upper(),),
    ).fetchall()
    return [
        ListingWindow(
            security_id=int(row[0]),
            symbol=row[1],
            exchange=row[2],
            valid_from=row[3],
            valid_to=row[4],
            is_primary=bool(row[5]),
        )
        for row in rows
    ]


def current_symbol(conn: sqlite3.Connection, security_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT symbol
          FROM listings
         WHERE security_id = ? AND valid_to IS NULL
         ORDER BY is_primary DESC
         LIMIT 1
        """,
        (security_id,),
    ).fetchone()
    return row[0] if row else None


def get_security(conn: sqlite3.Connection, security_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM securities WHERE security_id = ?", (security_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row) if isinstance(row, sqlite3.Row) else dict(zip(
        [d[0] for d in conn.execute("SELECT * FROM securities LIMIT 0").description], row
    ))


def mark_delisted(
    conn: sqlite3.Connection, security_id: int, delisted_date: str, close_listings: bool = True
) -> None:
    """Delisting is a soft state change. Rows are never deleted, so security_id
    is never freed for reuse."""
    conn.execute(
        "UPDATE securities SET is_active = 0, delisted_date = ?, last_seen = ? WHERE security_id = ?",
        (delisted_date, delisted_date, security_id),
    )
    if close_listings:
        conn.execute(
            "UPDATE listings SET valid_to = ? WHERE security_id = ? AND valid_to IS NULL",
            (delisted_date, security_id),
        )
