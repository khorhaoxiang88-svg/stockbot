"""Raw payload preservation.

Every SEC response is written to disk gzip-compressed and addressed by the
sha256 of its UNCOMPRESSED bytes:

    data/raw/{source}/{yyyy}/{mm}/{content_hash}.json.gz

SQLite stores only metadata. The hash is verified before anything is
reprocessed, and a mismatch raises rather than being repaired or ignored: a
payload that does not match its recorded hash is evidence of corruption, and
silently continuing would poison everything derived from it.
"""

from __future__ import annotations

import gzip
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAW_ROOT = REPO_ROOT / "data" / "raw"


class PayloadCorruptError(RuntimeError):
    """Raised when a stored payload does not match its recorded content hash."""


class PayloadMissingError(RuntimeError):
    """Raised when a payload recorded in the database is not on disk."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def relative_path_for(source: str, digest: str, fetched: datetime | None = None) -> str:
    moment = fetched or datetime.now(timezone.utc)
    return f"data/raw/{source}/{moment.strftime('%Y')}/{moment.strftime('%m')}/{digest}.json.gz"


def store_payload(
    conn: sqlite3.Connection,
    raw_bytes: bytes,
    source: str,
    endpoint: str,
    identifier: str,
    repo_root: Path | None = None,
) -> tuple[str, bool]:
    """Write the payload to disk and record it. Returns (payload_id, is_new).

    Identical content for the same (source, endpoint, identifier) reuses the
    existing row, which is what makes re-ingestion a no-op instead of a
    duplicate.
    """
    root = repo_root or REPO_ROOT
    digest = content_hash(raw_bytes)

    existing = conn.execute(
        "SELECT payload_id FROM raw_payloads "
        "WHERE source = ? AND endpoint = ? AND identifier = ? AND content_hash = ?",
        (source, endpoint, identifier, digest),
    ).fetchone()
    if existing:
        return existing[0], False

    now = datetime.now(timezone.utc)
    relative = relative_path_for(source, digest, now)
    absolute = root / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if not absolute.exists():
        # mtime is pinned so the file is byte-identical for a given payload.
        with gzip.GzipFile(filename="", mode="wb", fileobj=absolute.open("wb"), mtime=0) as handle:
            handle.write(raw_bytes)

    payload_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO raw_payloads
            (payload_id, source, endpoint, identifier, relative_path, content_hash,
             byte_size, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload_id,
            source,
            endpoint,
            identifier,
            relative,
            digest,
            len(raw_bytes),
            now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )
    return payload_id, True


def read_payload(
    conn: sqlite3.Connection, payload_id: str, repo_root: Path | None = None
) -> bytes:
    """Read a payload back, verifying its hash first. Raises on any mismatch."""
    root = repo_root or REPO_ROOT
    row = conn.execute(
        "SELECT relative_path, content_hash, byte_size FROM raw_payloads WHERE payload_id = ?",
        (payload_id,),
    ).fetchone()
    if row is None:
        raise PayloadMissingError(f"No payload recorded with payload_id {payload_id}")

    path = root / row["relative_path"]
    if not path.is_file():
        raise PayloadMissingError(f"Payload file missing on disk: {path}")

    with gzip.open(path, "rb") as handle:
        raw_bytes = handle.read()

    actual = content_hash(raw_bytes)
    if actual != row["content_hash"]:
        raise PayloadCorruptError(
            f"Payload {payload_id} failed hash verification.\n"
            f"  file     : {path}\n"
            f"  expected : {row['content_hash']}\n"
            f"  actual   : {actual}\n"
            "Refusing to reprocess a payload that does not match its recorded hash."
        )
    if len(raw_bytes) != row["byte_size"]:
        raise PayloadCorruptError(
            f"Payload {payload_id} size mismatch: recorded {row['byte_size']}, "
            f"found {len(raw_bytes)}"
        )
    return raw_bytes


def verify_all_payloads(
    conn: sqlite3.Connection, repo_root: Path | None = None, limit: int | None = None
) -> dict:
    """Verify every recorded payload. Returns a report; never raises on a bad one."""
    query = "SELECT payload_id FROM raw_payloads ORDER BY fetched_at"
    if limit:
        query += f" LIMIT {int(limit)}"
    ok, corrupt, missing = 0, [], []
    for row in conn.execute(query).fetchall():
        try:
            read_payload(conn, row["payload_id"], repo_root)
            ok += 1
        except PayloadCorruptError as exc:
            corrupt.append((row["payload_id"], str(exc)))
        except PayloadMissingError as exc:
            missing.append((row["payload_id"], str(exc)))
    return {"verified": ok, "corrupt": corrupt, "missing": missing}
