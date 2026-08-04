"""Shared security-selector for the S1 candidate pool.

F3, F4 and F5's ingestion scripts (prices/ingest.py, sec/ingest_facts.py,
fundamentals/compute.py) each already have their own `fixture_securities` /
`fixture_ciks`, scoped to fixture_manifest. This module is the S1 analogue,
scoped to universe_candidate_pool instead -- kept as one shared function
rather than three more copies, since all three want the same shape.
"""

from __future__ import annotations

import sqlite3


def pool_securities(conn: sqlite3.Connection, pool_version: str) -> list[dict]:
    """Every security in the named pool, current symbol, cik and class_count.

    class_count mirrors fundamentals.compute.fixture_securities: how many
    securities rows share this CIK, needed there to detect multi-class
    issuers. Rows with no CIK are excluded -- nothing downstream (SEC facts,
    fundamentals) can run without one, and the entry rule requires a CIK
    anyway.
    """
    rows = conn.execute(
        """
        SELECT s.security_id, s.cik, s.sic_code,
               COALESCE(l.symbol, p.symbol_at_discovery) AS symbol,
               (SELECT COUNT(*) FROM securities s2 WHERE s2.cik = s.cik) AS class_count
          FROM universe_candidate_pool p
          JOIN securities s ON s.security_id = p.security_id
          LEFT JOIN listings l ON l.security_id = p.security_id AND l.valid_to IS NULL
         WHERE p.pool_version = ? AND s.cik IS NOT NULL
         ORDER BY p.security_id
        """,
        (pool_version,),
    ).fetchall()
    return [dict(row) for row in rows]
