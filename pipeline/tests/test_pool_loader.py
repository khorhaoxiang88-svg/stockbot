"""S1 pool loading resolves sic_code from SEC submissions.

Offline: a fake SEC client serves canned submissions payloads by CIK, so
these tests never touch the network.
"""

import pytest

import migrate
from universe import identity, pool_loader
from universe.classify import Classification


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "pool.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


class FakeSecClient:
    """Serves canned submissions payloads by CIK."""

    def __init__(self, sic_by_cik):
        self.sic_by_cik = sic_by_cik
        self.calls = []

    def fetch_submissions(self, cik):
        self.calls.append(cik)
        if cik not in self.sic_by_cik:
            raise RuntimeError(f"no fixture for cik {cik}")
        return {"sic": self.sic_by_cik[cik]}


class FailingSecClient:
    def fetch_submissions(self, cik):
        raise RuntimeError("SEC is down")


def make_candidate(symbol, cik, name=None, security_type="common_stock", confidence="high"):
    return {
        "symbol": symbol,
        "security_name": name or f"{symbol} Inc.",
        "exchange": "Nasdaq",
        "cik": cik,
        "classification": Classification(security_type, confidence, "test"),
    }


def sic_of(conn, security_id):
    return conn.execute(
        "SELECT sic_code FROM securities WHERE security_id = ?", (security_id,)
    ).fetchone()[0]


def test_new_pool_security_gets_sic_code_from_submissions(conn):
    sec = FakeSecClient({"0000000111": "7372"})
    candidates = [make_candidate("ABCD", "0000000111")]

    results = pool_loader.load_pool(conn, candidates, "test-pool", sec)

    assert sic_of(conn, results[0]["security_id"]) == "7372"


def test_existing_security_missing_sic_code_is_backfilled_on_rerun(conn):
    """Regression: before this fix, pool_loader hardcoded sic_code=None and
    never revisited an already-created security, so a pool security already
    in the database would stay SIC-UNKNOWN forever even after the loader
    learned how to fetch it -- unless re-running the loader also checks
    existing rows, not just newly created ones."""
    security_id = identity.create_security(
        conn, name="ABCD Inc.", cik="0000000111", security_type="common_stock",
        classification_confidence="high", classification_source="test",
        sic_code=None, first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )
    sec = FakeSecClient({"0000000111": "7372"})
    candidates = [make_candidate("ABCD", "0000000111")]

    pool_loader.load_pool(conn, candidates, "test-pool", sec)

    assert sic_of(conn, security_id) == "7372"


def test_security_that_already_has_sic_code_is_not_refetched(conn):
    security_id = identity.create_security(
        conn, name="ABCD Inc.", cik="0000000111", security_type="common_stock",
        classification_confidence="high", classification_source="test",
        sic_code="1234", first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )
    sec = FakeSecClient({"0000000111": "7372"})
    candidates = [make_candidate("ABCD", "0000000111")]

    pool_loader.load_pool(conn, candidates, "test-pool", sec)

    assert sec.calls == [], "a security that already has a sic_code must not be re-fetched"
    assert sic_of(conn, security_id) == "1234"


def test_submissions_fetch_failure_leaves_sic_code_null_without_crashing(conn):
    candidates = [make_candidate("ABCD", "0000000111")]
    results = pool_loader.load_pool(conn, candidates, "test-pool", FailingSecClient())

    assert sic_of(conn, results[0]["security_id"]) is None


def test_two_securities_sharing_a_cik_fetch_submissions_only_once(conn):
    """A preferred and common share of the same issuer share a CIK. The
    submissions call (and the SIC it returns) is cached per run rather than
    repeated for every security under that CIK."""
    sec = FakeSecClient({"0000000111": "6021"})
    candidates = [
        make_candidate("ABCD", "0000000111", name="ABCD Common"),
        make_candidate(
            "ABCD.PR", "0000000111", name="ABCD Preferred", security_type="preferred_share"
        ),
    ]

    results = pool_loader.load_pool(conn, candidates, "test-pool", sec)

    assert sec.calls == ["0000000111"]
    assert sic_of(conn, results[0]["security_id"]) == "6021"
    assert sic_of(conn, results[1]["security_id"]) == "6021"


def test_candidate_with_no_cik_is_left_with_null_sic_code(conn):
    sec = FakeSecClient({})
    candidates = [make_candidate("ABCD", None)]

    results = pool_loader.load_pool(conn, candidates, "test-pool", sec)

    assert sec.calls == []
    assert sic_of(conn, results[0]["security_id"]) is None
