"""Per-item resumability bookkeeping for scaled ingestion (S2, migration 017).

One row per (batch_id, stage, item_key). Re-invoking the orchestrator with the
SAME batch_id after a kill skips items already 'success' and retries 'failed'
or never-attempted ones. This is operational state, not evidence: unlike
research_candidates or filing_verifications, overwriting the row for a retried
item is correct, the same way source_health tracks current state rather than
a permanent log.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_item(
    conn: sqlite3.Connection,
    batch_id: str,
    stage: str,
    item_key: str,
    status: str,
    run_id: str,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO orchestration_progress
            (batch_id, stage, item_key, status, attempted_at, error, run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (batch_id, stage, item_key, status, utc_now_iso(), error, run_id),
    )


def pending_items(
    conn: sqlite3.Connection, batch_id: str, stage: str, all_item_keys: list[str]
) -> list[str]:
    """all_item_keys minus whatever is already 'success' or 'skipped' for this
    batch_id+stage. Order is preserved so resuming continues where it left
    off rather than in a different order each time."""
    done = {
        row[0]
        for row in conn.execute(
            "SELECT item_key FROM orchestration_progress "
            "WHERE batch_id = ? AND stage = ? AND status IN ('success', 'skipped')",
            (batch_id, stage),
        )
    }
    return [key for key in all_item_keys if key not in done]


def batch_summary(conn: sqlite3.Connection, batch_id: str, stage: str) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM orchestration_progress "
        "WHERE batch_id = ? AND stage = ? GROUP BY status",
        (batch_id, stage),
    ).fetchall()
    counts = {row[0]: row[1] for row in rows}
    return {
        "success": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "skipped": counts.get("skipped", 0),
    }
