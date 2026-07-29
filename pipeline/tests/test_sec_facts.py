"""Payload preservation and append-only fact storage.

Offline: a fake SEC client serves hand-built payloads, so these tests never
touch the network.
"""

import gzip
import json
import sqlite3

import pytest

import migrate
from sec import ingest_facts
from sec.acceptance import build_filing_index, normalise_acceptance
from sec.facts import compute_semantic_hash, iter_facts
from sec.payload_store import (
    PayloadCorruptError,
    PayloadMissingError,
    read_payload,
    store_payload,
    verify_all_payloads,
)


class FakeResponse:
    def __init__(self, payload):
        self.content = json.dumps(payload).encode("utf-8")

    def json(self):
        return json.loads(self.content)


class FakeSecClient:
    """Serves canned payloads by URL substring."""

    def __init__(self, companyfacts, submissions):
        self.companyfacts = companyfacts
        self.submissions = submissions
        self.calls = []

    def _get(self, url, timeout=30):
        self.calls.append(url)
        if "companyfacts" in url:
            return FakeResponse(self.companyfacts)
        if "submissions" in url:
            return FakeResponse(self.submissions)
        raise AssertionError(f"unexpected url {url}")


CIK = "0000000042"

# Two entries with IDENTICAL semantic fields (same concept, unit, period) but
# reported by different filings with different values: a restatement.
COMPANYFACTS = {
    "cik": 42,
    "entityName": "Test Corp",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "description": "Total revenue",
                "units": {
                    "USD": [
                        {
                            "start": "2023-01-01", "end": "2023-12-31", "val": 1000,
                            "accn": "0000000042-24-000001", "fy": 2023, "fp": "FY",
                            "form": "10-K", "filed": "2024-02-01",
                        },
                        {
                            "start": "2023-01-01", "end": "2023-12-31", "val": 1250,
                            "accn": "0000000042-25-000002", "fy": 2023, "fp": "FY",
                            "form": "10-K/A", "filed": "2025-02-01",
                        },
                        {
                            "start": "2024-01-01", "end": "2024-12-31", "val": 2000,
                            "accn": "0000000042-25-000002", "fy": 2024, "fp": "FY",
                            "form": "10-K", "filed": "2025-02-01",
                        },
                    ]
                },
            },
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        {
                            "end": "2024-12-31", "val": 5000,
                            "accn": "0000000042-25-000002", "fy": 2024, "fp": "FY",
                            "form": "10-K", "filed": "2025-02-01",
                        },
                        # Same accession, no acceptance record -> unusable.
                        {
                            "end": "2022-12-31", "val": 4000,
                            "accn": "0000000042-99-999999", "fy": 2022, "fp": "FY",
                            "form": "10-K", "filed": "2023-02-01",
                        },
                    ]
                },
            },
        }
    },
}

SUBMISSIONS = {
    "cik": "42",
    "filings": {
        "recent": {
            "accessionNumber": ["0000000042-24-000001", "0000000042-25-000002"],
            "form": ["10-K", "10-K/A"],
            "filingDate": ["2024-02-01", "2025-02-01"],
            "acceptanceDateTime": ["2024-02-01T21:30:00.000Z", "2025-02-01T22:05:11.000Z"],
            "reportDate": ["2023-12-31", "2024-12-31"],
            "primaryDocument": ["a.htm", "b.htm"],
        },
        "files": [],
    },
}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("sec.payload_store.REPO_ROOT", tmp_path)
    connection = migrate.connect(tmp_path / "facts.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(tmp_path):
    return tmp_path


def run_ingest(conn, repo, client=None):
    client = client or FakeSecClient(COMPANYFACTS, SUBMISSIONS)
    report = ingest_facts.FactsReport()
    ingest_facts.ingest_company(conn, client, CIK, "TEST", report)
    return report


# ------------------------------------------------- 1. source identity is kept


def test_identical_semantics_different_source_key_are_both_stored(conn, repo):
    run_ingest(conn, repo)

    rows = conn.execute(
        """
        SELECT source_fact_key, semantic_hash, normalized_numeric_value, accession_no
          FROM xbrl_facts
         WHERE concept = 'Revenues' AND period_end = '2023-12-31'
         ORDER BY source_fact_key
        """
    ).fetchall()

    assert len(rows) == 2, "both source facts must survive"
    assert rows[0]["semantic_hash"] == rows[1]["semantic_hash"], "same meaning"
    assert rows[0]["source_fact_key"] != rows[1]["source_fact_key"], "different source identity"
    assert {r["normalized_numeric_value"] for r in rows} == {1000.0, 1250.0}
    assert {r["accession_no"] for r in rows} == {
        "0000000042-24-000001",
        "0000000042-25-000002",
    }


def test_semantic_hash_is_not_unique_and_is_not_a_constraint(conn, repo):
    run_ingest(conn, repo)
    duplicated = conn.execute(
        "SELECT COUNT(*) FROM (SELECT semantic_hash FROM xbrl_facts "
        "GROUP BY semantic_hash HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert duplicated >= 1

    indexes = [
        row[1]
        for row in conn.execute("PRAGMA index_list(xbrl_facts)")
        if row[2]  # unique flag
    ]
    for index_name in indexes:
        columns = {row[2] for row in conn.execute(f"PRAGMA index_info({index_name})")}
        assert "semantic_hash" not in columns, "semantic_hash must never be a uniqueness key"


def test_unique_constraint_is_on_payload_and_source_fact_key(conn, repo):
    run_ingest(conn, repo)
    payload_id = conn.execute("SELECT payload_id FROM xbrl_facts LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO xbrl_facts (payload_id, source_fact_key, cik, taxonomy, concept,
                                    context_hash, semantic_hash, source_endpoint)
            SELECT payload_id, source_fact_key, cik, taxonomy, concept,
                   context_hash, semantic_hash, source_endpoint
              FROM xbrl_facts WHERE payload_id = ? LIMIT 1
            """,
            (payload_id,),
        )


# --------------------------------------------------------- 2. re-ingest is safe


def test_reingesting_the_same_payload_does_not_duplicate_facts(conn, repo):
    first = run_ingest(conn, repo)
    count_after_first = conn.execute("SELECT COUNT(*) FROM xbrl_facts").fetchone()[0]
    assert count_after_first == first.facts_written == 5

    second = run_ingest(conn, repo)
    count_after_second = conn.execute("SELECT COUNT(*) FROM xbrl_facts").fetchone()[0]

    assert count_after_second == count_after_first
    assert second.facts_written == 0
    assert second.facts_skipped_existing == 5
    assert second.payloads_reused >= 1
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 2


# ------------------------------------------------------------ 3. restatements


def test_restatement_is_two_rows_from_two_accessions(conn, repo):
    run_ingest(conn, repo)

    semantic = compute_semantic_hash(
        "us-gaap", "Revenues", "USD", "duration", "2023-01-01", "2023-12-31", None
    )
    rows = conn.execute(
        "SELECT normalized_numeric_value, accession_no, form_type, filed_date "
        "FROM xbrl_facts WHERE semantic_hash = ? ORDER BY filed_date",
        (semantic,),
    ).fetchall()

    assert len(rows) == 2, "a restatement is two rows, not one overwritten row"
    assert rows[0]["normalized_numeric_value"] == 1000.0
    assert rows[0]["form_type"] == "10-K"
    assert rows[1]["normalized_numeric_value"] == 1250.0
    assert rows[1]["form_type"] == "10-K/A"
    assert rows[0]["accession_no"] != rows[1]["accession_no"]


def test_append_only_update_is_blocked_by_the_database(conn, repo):
    run_ingest(conn, repo)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE xbrl_facts SET normalized_numeric_value = 1 WHERE fact_id = 1")


def test_append_only_delete_is_blocked_by_the_database(conn, repo):
    run_ingest(conn, repo)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM xbrl_facts WHERE fact_id = 1")


# ------------------------------------------------- 4. hash verification is loud


def test_payload_round_trips_and_verifies(conn, repo):
    raw = b'{"hello": "world"}'
    payload_id, is_new = store_payload(conn, raw, "sec", "companyfacts", "CIK1", repo_root=repo)
    assert is_new is True
    assert read_payload(conn, payload_id, repo_root=repo) == raw


def test_corrupted_payload_raises_loudly(conn, repo):
    raw = b'{"hello": "world"}'
    payload_id, _ = store_payload(conn, raw, "sec", "companyfacts", "CIK1", repo_root=repo)

    relative = conn.execute(
        "SELECT relative_path FROM raw_payloads WHERE payload_id = ?", (payload_id,)
    ).fetchone()[0]
    path = repo / relative
    with gzip.open(path, "wb") as handle:
        handle.write(b'{"hello": "TAMPERED"}')

    with pytest.raises(PayloadCorruptError) as exc:
        read_payload(conn, payload_id, repo_root=repo)
    message = str(exc.value)
    assert "failed hash verification" in message
    assert "expected" in message and "actual" in message


def test_missing_payload_file_raises(conn, repo):
    raw = b'{"a": 1}'
    payload_id, _ = store_payload(conn, raw, "sec", "companyfacts", "CIK1", repo_root=repo)
    relative = conn.execute(
        "SELECT relative_path FROM raw_payloads WHERE payload_id = ?", (payload_id,)
    ).fetchone()[0]
    (repo / relative).unlink()
    with pytest.raises(PayloadMissingError):
        read_payload(conn, payload_id, repo_root=repo)


def test_verify_all_payloads_reports_corruption_without_raising(conn, repo):
    good, _ = store_payload(conn, b'{"a": 1}', "sec", "companyfacts", "CIK1", repo_root=repo)
    bad, _ = store_payload(conn, b'{"b": 2}', "sec", "companyfacts", "CIK2", repo_root=repo)
    relative = conn.execute(
        "SELECT relative_path FROM raw_payloads WHERE payload_id = ?", (bad,)
    ).fetchone()[0]
    with gzip.open(repo / relative, "wb") as handle:
        handle.write(b"corrupted")

    report = verify_all_payloads(conn, repo_root=repo)
    assert report["verified"] == 1
    assert len(report["corrupt"]) == 1
    assert report["corrupt"][0][0] == bad


def test_payload_is_stored_compressed_in_the_dated_hierarchy(conn, repo):
    raw = json.dumps(COMPANYFACTS).encode("utf-8")
    payload_id, _ = store_payload(conn, raw, "sec", "companyfacts", "CIK42", repo_root=repo)
    row = conn.execute(
        "SELECT relative_path, content_hash, byte_size FROM raw_payloads WHERE payload_id = ?",
        (payload_id,),
    ).fetchone()

    assert row["relative_path"].startswith("data/raw/sec/")
    assert row["relative_path"].endswith(".json.gz")
    assert row["content_hash"] in row["relative_path"]
    assert row["byte_size"] == len(raw)

    path = repo / row["relative_path"]
    assert path.is_file()
    assert path.stat().st_size < len(raw), "payload must actually be compressed"
    parts = row["relative_path"].split("/")
    assert len(parts[3]) == 4 and parts[3].isdigit(), "yyyy directory"
    assert len(parts[4]) == 2 and parts[4].isdigit(), "mm directory"


# ------------------------------------------- 5. unresolvable accepted_at flagged


def test_fact_without_resolvable_acceptance_is_flagged_unusable(conn, repo):
    run_ingest(conn, repo)

    unusable = conn.execute(
        "SELECT concept, period_end, accession_no, accepted_at FROM xbrl_facts "
        "WHERE accepted_at IS NULL"
    ).fetchall()
    assert len(unusable) == 1
    assert unusable[0]["accession_no"] == "0000000042-99-999999"
    assert unusable[0]["accepted_at"] is None

    # The usable_facts view is the single place that rule is expressed.
    assert conn.execute("SELECT COUNT(*) FROM usable_facts").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM xbrl_facts").fetchone()[0] == 5
    excluded = conn.execute(
        "SELECT COUNT(*) FROM usable_facts WHERE accession_no = '0000000042-99-999999'"
    ).fetchone()[0]
    assert excluded == 0


def test_acceptance_timestamps_are_utc_to_the_second(conn, repo):
    run_ingest(conn, repo)
    for row in conn.execute(
        "SELECT accepted_at FROM xbrl_facts WHERE accepted_at IS NOT NULL"
    ):
        assert row["accepted_at"].endswith("Z")
        assert len(row["accepted_at"]) == 20


def test_normalise_acceptance_handles_edgar_shapes():
    assert normalise_acceptance("2026-06-17T22:40:43.000Z") == "2026-06-17T22:40:43Z"
    assert normalise_acceptance("2026-06-17 22:40:43") == "2026-06-17T22:40:43Z"
    assert normalise_acceptance("") is None
    assert normalise_acceptance(None) is None


# ------------------------------------------------------- known limitation kept


def test_companyfacts_limitations_are_stored_as_null_not_fabricated(conn, repo):
    run_ingest(conn, repo)
    row = conn.execute(
        "SELECT COUNT(*) n, "
        "SUM(CASE WHEN decimals IS NOT NULL THEN 1 ELSE 0 END) d, "
        "SUM(CASE WHEN is_nil IS NOT NULL THEN 1 ELSE 0 END) nil, "
        "SUM(CASE WHEN dimensions_json IS NOT NULL THEN 1 ELSE 0 END) dim "
        "FROM xbrl_facts"
    ).fetchone()
    assert row["n"] == 5
    assert row["d"] == 0, "decimals is not available from companyfacts"
    assert row["nil"] == 0, "nil flags are not available from companyfacts"
    assert row["dim"] == 0, "dimensional members are not available from companyfacts"

    endpoints = {r[0] for r in conn.execute("SELECT DISTINCT source_endpoint FROM xbrl_facts")}
    assert endpoints == {"companyfacts"}


def test_context_type_reflects_instant_versus_duration(conn, repo):
    run_ingest(conn, repo)
    revenues = conn.execute(
        "SELECT DISTINCT context_type FROM xbrl_facts WHERE concept = 'Revenues'"
    ).fetchone()[0]
    assets = conn.execute(
        "SELECT DISTINCT context_type FROM xbrl_facts WHERE concept = 'Assets'"
    ).fetchone()[0]
    assert revenues == "duration"
    assert assets == "instant"


def test_source_fact_key_encodes_position_in_the_source_document():
    keys = [fact.source_fact_key for fact in iter_facts(COMPANYFACTS)]
    assert "us-gaap|Revenues|USD|0" in keys
    assert "us-gaap|Revenues|USD|1" in keys
    assert len(keys) == len(set(keys)), "source keys must be unique within a payload"


def test_filing_index_uses_paginated_submission_pages():
    client = FakeSecClient(COMPANYFACTS, SUBMISSIONS)
    index, payloads = build_filing_index(client, CIK)
    assert set(index) == {"0000000042-24-000001", "0000000042-25-000002"}
    assert index["0000000042-24-000001"].accepted_at == "2024-02-01T21:30:00Z"
    assert index["0000000042-25-000002"].period_of_report == "2024-12-31"
    assert len(payloads) == 1
