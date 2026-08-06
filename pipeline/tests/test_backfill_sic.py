"""One-time sic_code backfill for existing SIC-UNKNOWN securities.

Offline: a fake SEC client serves canned submissions payloads by CIK, so
these tests never touch the network.
"""

import pytest

import migrate
from universe import backfill_sic, identity


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "backfill.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


class FakeSecClient:
    def __init__(self, sic_by_cik):
        self.sic_by_cik = sic_by_cik
        self.calls = []

    def fetch_submissions(self, cik):
        self.calls.append(cik)
        if cik not in self.sic_by_cik:
            raise RuntimeError(f"no fixture for cik {cik}")
        return {"sic": self.sic_by_cik[cik]}


def make_security(conn, name, cik=None, sic_code=None, security_type="common_stock"):
    return identity.create_security(
        conn, name=name, cik=cik, security_type=security_type,
        classification_confidence="high", classification_source="test",
        sic_code=sic_code, first_seen="2026-01-01T00:00:00Z",
        last_seen="2026-01-01T00:00:00Z",
    )


def sic_of(conn, security_id):
    return conn.execute(
        "SELECT sic_code FROM securities WHERE security_id = ?", (security_id,)
    ).fetchone()[0]


def test_only_cik_present_and_sic_null_rows_are_touched(conn):
    missing = make_security(conn, "Missing SIC Inc.", cik="0000000111")
    already_has = make_security(conn, "Already Known Inc.", cik="0000000222", sic_code="7372")
    no_cik = make_security(conn, "No CIK Inc.", cik=None)

    sec = FakeSecClient({"0000000111": "6021"})
    summary = backfill_sic.backfill(conn, sec)

    assert summary == {
        "candidates": 1,
        "updated": 1,
        "no_sic_on_file": 0,
        "failed": 0,
    }
    assert sic_of(conn, missing) == "6021"
    assert sic_of(conn, already_has) == "7372", "must not touch a row that already has a sic_code"
    assert sic_of(conn, no_cik) is None
    assert sec.calls == ["0000000111"], "must not fetch submissions for a security with no CIK"


def test_securities_sharing_a_cik_fetch_submissions_once(conn):
    common = make_security(conn, "ABCD Common", cik="0000000111", security_type="common_stock")
    preferred = make_security(
        conn, "ABCD Preferred", cik="0000000111", security_type="preferred_share"
    )

    sec = FakeSecClient({"0000000111": "6021"})
    summary = backfill_sic.backfill(conn, sec)

    assert summary["updated"] == 2
    assert sec.calls == ["0000000111"]
    assert sic_of(conn, common) == "6021"
    assert sic_of(conn, preferred) == "6021"


def test_no_sic_on_file_is_counted_not_silently_dropped(conn):
    security_id = make_security(conn, "No SIC Filed Inc.", cik="0000000333")
    sec = FakeSecClient({"0000000333": None})

    summary = backfill_sic.backfill(conn, sec)

    assert summary == {"candidates": 1, "updated": 0, "no_sic_on_file": 1, "failed": 0}
    assert sic_of(conn, security_id) is None


def test_fetch_failure_is_counted_and_does_not_stop_the_run(conn):
    class PartiallyFailingSecClient:
        def __init__(self):
            self.calls = []

        def fetch_submissions(self, cik):
            self.calls.append(cik)
            if cik == "0000000111":
                raise RuntimeError("SEC is down")
            return {"sic": "6021"}

    fails = make_security(conn, "Down Inc.", cik="0000000111")
    succeeds = make_security(conn, "Up Inc.", cik="0000000222")

    sec = PartiallyFailingSecClient()
    summary = backfill_sic.backfill(conn, sec)

    assert summary == {"candidates": 2, "updated": 1, "no_sic_on_file": 0, "failed": 1}
    assert sic_of(conn, fails) is None
    assert sic_of(conn, succeeds) == "6021"


def test_records_a_pipeline_run(conn):
    make_security(conn, "ABCD Inc.", cik="0000000111")
    sec = FakeSecClient({"0000000111": "6021"})

    backfill_sic.backfill(conn, sec)

    run = conn.execute(
        "SELECT stage, status, records_written FROM pipeline_runs WHERE stage = 'sic_backfill'"
    ).fetchone()
    assert run is not None
    assert run["status"] == "success"
    assert run["records_written"] == 1


def test_a_second_run_is_a_no_op_once_everything_resolved(conn):
    security_id = make_security(conn, "ABCD Inc.", cik="0000000111")
    sec = FakeSecClient({"0000000111": "6021"})

    backfill_sic.backfill(conn, sec)
    second_summary = backfill_sic.backfill(conn, sec)

    assert second_summary == {"candidates": 0, "updated": 0, "no_sic_on_file": 0, "failed": 0}
    assert sic_of(conn, security_id) == "6021"
