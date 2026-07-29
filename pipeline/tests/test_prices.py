"""Price ingestion, revision auditing, adjustment, and version reconstruction.

All offline: a fake provider supplies deterministic bars, so these tests never
touch the network and never depend on what a vendor happens to be serving.
"""

import sqlite3

import pytest

import migrate
from prices import ingest as ingest_module
from prices.adjust import (
    adjusted_series,
    largest_single_day_return,
    return_across_date,
    split_factor_for_date,
)
from prices.base import CorporateActionRecord, PriceBar, PriceProvider
from prices.ingest import current_dataset_version, ingest_securities
from prices.versions import price_state_as_of
from universe import identity


class FakeProvider(PriceProvider):
    """Serves whatever bars the test hands it."""

    def __init__(self, bars=None, actions=None, name="fake"):
        self._bars = list(bars or [])
        self._actions = list(actions or [])
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def provider_symbol(self, exchange_symbol: str) -> str:
        return exchange_symbol.upper()

    def fetch_daily_bars(self, exchange_symbol: str, years: int = 3):
        return list(self._bars)

    def fetch_corporate_actions(self, exchange_symbol: str, years: int = 3):
        return list(self._actions)

    def set_bars(self, bars):
        self._bars = list(bars)


BASE_BARS = [
    PriceBar("2026-01-02", 100.0, 101.0, 99.0, 100.5, 1_000_000),
    PriceBar("2026-01-05", 100.5, 102.0, 100.0, 101.5, 1_100_000),
    PriceBar("2026-01-06", 101.5, 103.0, 101.0, 102.5, 1_200_000),
]


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "prices.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


@pytest.fixture
def security(conn):
    security_id = identity.create_security(
        conn,
        name="Test Corp Common Stock",
        cik="0000000042",
        security_type="common_stock",
        classification_confidence="high",
        classification_source="test",
        first_seen="2026-01-01T00:00:00Z",
        last_seen="2026-01-01T00:00:00Z",
    )
    identity.add_listing(
        conn, security_id=security_id, symbol="TEST", exchange="NYSE", valid_from="2020-01-01"
    )
    return security_id


# ------------------------------------------------------------- 1. idempotency


def test_reingesting_unchanged_data_creates_no_new_dataset_version(conn, security):
    provider = FakeProvider(BASE_BARS)
    first = ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    assert first.rows_inserted == 3
    assert first.dataset_version_after == 1

    second = ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    assert second.rows_inserted == 0
    assert second.revisions_detected == 0
    assert second.rows_unchanged == 3
    assert second.dataset_version_after == 1, "unchanged data must not bump the version"
    assert current_dataset_version(conn) == 1

    versions = conn.execute("SELECT COUNT(*) FROM price_dataset_versions").fetchone()[0]
    assert versions == 1
    assert conn.execute("SELECT COUNT(*) FROM price_revisions").fetchone()[0] == 0


def test_reingest_still_refreshes_last_verified_at(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    conn.execute("UPDATE prices SET last_verified_at = '2000-01-01T00:00:00Z'")
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    stale = conn.execute(
        "SELECT COUNT(*) FROM prices WHERE last_verified_at = '2000-01-01T00:00:00Z'"
    ).fetchone()[0]
    assert stale == 0


def test_float_noise_below_tolerance_is_not_a_revision(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)

    nudged = list(BASE_BARS)
    nudged[1] = PriceBar("2026-01-05", 100.5, 102.0, 100.0, 101.5001, 1_100_000)
    provider.set_bars(nudged)
    report = ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    assert report.revisions_detected == 0
    assert report.dataset_version_after == 1


# ------------------------------------------------------- 2. injected correction


def test_injected_correction_is_detected_and_fully_audited(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    assert current_dataset_version(conn) == 1

    # The vendor "corrects" one bar: different close and different volume.
    corrected = list(BASE_BARS)
    corrected[1] = PriceBar("2026-01-05", 100.5, 105.0, 100.0, 104.25, 2_500_000)
    provider.set_bars(corrected)

    report = ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    assert report.revisions_detected == 1
    assert report.dataset_version_after == 2, "a correction must bump the global version"

    audit = conn.execute(
        "SELECT * FROM price_revisions WHERE security_id = ? AND date = '2026-01-05'",
        (security,),
    ).fetchone()
    assert audit is not None

    # Complete old values.
    assert audit["old_open"] == 100.5
    assert audit["old_high"] == 102.0
    assert audit["old_low"] == 100.0
    assert audit["old_close"] == 101.5
    assert audit["old_volume"] == 1_100_000
    # Complete new values.
    assert audit["new_open"] == 100.5
    assert audit["new_high"] == 105.0
    assert audit["new_low"] == 100.0
    assert audit["new_close"] == 104.25
    assert audit["new_volume"] == 2_500_000
    # Version either side, and an audit trail with timestamps.
    assert audit["price_data_version_before"] == 1
    assert audit["price_data_version_after"] == 2
    assert audit["revision"] == 1
    assert audit["detected_at"].endswith("Z")
    assert audit["provider"] == "fake"

    # The canonical row now carries the new values and an incremented revision.
    canonical = conn.execute(
        "SELECT * FROM prices WHERE security_id = ? AND date = '2026-01-05'", (security,)
    ).fetchone()
    assert canonical["close"] == 104.25
    assert canonical["volume"] == 2_500_000
    assert canonical["revision"] == 1
    assert canonical["price_data_version"] == 2

    # Untouched bars keep the old version, so the change is traceable.
    untouched = conn.execute(
        "SELECT price_data_version, revision FROM prices "
        "WHERE security_id = ? AND date = '2026-01-02'",
        (security,),
    ).fetchone()
    assert untouched["price_data_version"] == 1
    assert untouched["revision"] == 0


def test_second_correction_increments_revision_again(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)

    for new_close in (104.25, 106.75):
        bars = list(BASE_BARS)
        bars[1] = PriceBar("2026-01-05", 100.5, 105.0, 100.0, new_close, 2_500_000)
        provider.set_bars(bars)
        ingest_securities(conn, provider, [(security, "TEST")], verbose=False)

    revisions = conn.execute(
        "SELECT revision, old_close, new_close FROM price_revisions "
        "WHERE security_id = ? ORDER BY revision",
        (security,),
    ).fetchall()
    assert [r["revision"] for r in revisions] == [1, 2]
    assert revisions[0]["old_close"] == 101.5
    assert revisions[1]["old_close"] == 104.25
    assert revisions[1]["new_close"] == 106.75
    assert current_dataset_version(conn) == 3


def test_new_rows_also_create_a_version_but_not_a_revision(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)

    provider.set_bars(BASE_BARS + [PriceBar("2026-01-07", 102.5, 104.0, 102.0, 103.5, 900_000)])
    report = ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    assert report.rows_inserted == 1
    assert report.revisions_detected == 0
    assert report.dataset_version_after == 2


# ------------------------------------------------------------ 3. split adjustment


SPLIT_BARS = [
    # Pre-split: trading around 1200.
    PriceBar("2024-06-05", 1190.0, 1210.0, 1185.0, 1200.0, 40_000_000),
    PriceBar("2024-06-06", 1200.0, 1215.0, 1195.0, 1205.0, 41_000_000),
    PriceBar("2024-06-07", 1205.0, 1215.0, 1200.0, 1208.0, 41_238_600),
    # Ex-date: a 10-for-1 split, so the raw price drops by roughly 10x.
    PriceBar("2024-06-10", 120.0, 122.5, 119.0, 121.8, 313_434_100),
    PriceBar("2024-06-11", 121.8, 124.0, 121.0, 123.0, 300_000_000),
]
SPLIT_ACTION = CorporateActionRecord(ex_date="2024-06-10", action_type="split", ratio=10.0)


def test_known_split_produces_a_continuous_adjusted_series(conn, security):
    provider = FakeProvider(SPLIT_BARS, [SPLIT_ACTION])
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)

    raw_return = None
    rows = conn.execute(
        "SELECT date, close FROM prices WHERE security_id = ? ORDER BY date", (security,)
    ).fetchall()
    closes = {row["date"]: row["close"] for row in rows}
    raw_return = closes["2024-06-10"] / closes["2024-06-07"] - 1
    # The raw series must still show the real cliff. Raw means raw.
    assert raw_return < -0.85

    bars = adjusted_series(conn, security)
    adjusted_return = return_across_date(bars, "2024-06-10")
    assert adjusted_return is not None
    # No artificial return: the adjusted move across the ex-date is an ordinary day.
    assert abs(adjusted_return) < 0.05, f"artificial gap at the split: {adjusted_return:.2%}"

    worst, worst_date = largest_single_day_return(bars)
    assert worst < 0.05
    assert worst_date != "2024-06-10" or worst < 0.05

    # Pre-split bars are divided by the ratio, post-split bars are untouched.
    by_date = {bar.date: bar for bar in bars}
    assert by_date["2024-06-07"].close == pytest.approx(120.8, abs=1e-6)
    assert by_date["2024-06-07"].factor == 10.0
    assert by_date["2024-06-10"].close == pytest.approx(121.8, abs=1e-6)
    assert by_date["2024-06-10"].factor == 1.0
    # Volume moves the other way.
    assert by_date["2024-06-07"].volume == 412_386_000


def test_reverse_split_is_also_continuous(conn, security):
    bars = [
        PriceBar("2024-02-26", 0.50, 0.52, 0.49, 0.50, 10_000_000),
        PriceBar("2024-02-27", 25.0, 26.0, 24.5, 25.2, 200_000),
        PriceBar("2024-02-28", 25.2, 26.5, 25.0, 25.8, 210_000),
    ]
    action = CorporateActionRecord(ex_date="2024-02-27", action_type="split", ratio=0.02)
    ingest_securities(conn, FakeProvider(bars, [action]), [(security, "TEST")], verbose=False)

    adjusted = adjusted_series(conn, security)
    change = return_across_date(adjusted, "2024-02-27")
    assert change is not None
    assert abs(change) < 0.05


def test_split_flagged_for_review_is_not_applied(conn, security):
    action = CorporateActionRecord(
        ex_date="2024-06-10", action_type="split", ratio=10.0, requires_manual_review=True
    )
    ingest_securities(conn, FakeProvider(SPLIT_BARS, [action]), [(security, "TEST")], verbose=False)
    bars = adjusted_series(conn, security)
    # A suspicious ratio must not silently reshape history.
    assert all(bar.factor == 1.0 for bar in bars)


def test_split_factor_maths():
    splits = [("2024-06-10", 10.0), ("2025-01-15", 2.0)]
    assert split_factor_for_date(splits, "2024-06-09") == 20.0
    assert split_factor_for_date(splits, "2024-06-10") == 2.0
    assert split_factor_for_date(splits, "2025-01-15") == 1.0


def test_prices_table_has_no_adjusted_close_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(prices)")}
    assert "adj_close" not in columns
    assert "adjusted_close" not in columns
    assert {"open", "high", "low", "close", "volume"} <= columns


# -------------------------------------------------- 4. reconstruct earlier state


def test_can_reconstruct_price_state_as_of_an_earlier_version(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)  # v1

    corrected = list(BASE_BARS)
    corrected[1] = PriceBar("2026-01-05", 100.5, 105.0, 100.0, 104.25, 2_500_000)
    provider.set_bars(corrected)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)  # v2

    provider.set_bars(corrected + [PriceBar("2026-01-07", 104.25, 106.0, 104.0, 105.5, 800_000)])
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)  # v3

    at_v1 = {bar.date: bar for bar in price_state_as_of(conn, security, 1)}
    at_v2 = {bar.date: bar for bar in price_state_as_of(conn, security, 2)}
    at_v3 = {bar.date: bar for bar in price_state_as_of(conn, security, 3)}

    # v1: the original close, and the later bar does not exist yet.
    assert at_v1["2026-01-05"].close == 101.5
    assert at_v1["2026-01-05"].volume == 1_100_000
    assert at_v1["2026-01-05"].revision == 0
    assert "2026-01-07" not in at_v1
    assert len(at_v1) == 3

    # v2: correction applied, still no new bar.
    assert at_v2["2026-01-05"].close == 104.25
    assert at_v2["2026-01-05"].revision == 1
    assert "2026-01-07" not in at_v2

    # v3: new bar present.
    assert at_v3["2026-01-05"].close == 104.25
    assert "2026-01-07" in at_v3
    assert len(at_v3) == 4


def test_reconstruction_survives_two_corrections_to_one_bar(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)  # v1
    for close in (104.25, 106.75):
        bars = list(BASE_BARS)
        bars[1] = PriceBar("2026-01-05", 100.5, 105.0, 100.0, close, 2_500_000)
        provider.set_bars(bars)
        ingest_securities(conn, provider, [(security, "TEST")], verbose=False)  # v2, v3

    assert price_state_as_of(conn, security, 1)[1].close == 101.5
    assert price_state_as_of(conn, security, 2)[1].close == 104.25
    assert price_state_as_of(conn, security, 3)[1].close == 106.75


# ------------------------------------------------------------ provider contract


def test_provenance_is_recorded_once_per_provider(conn, security):
    provider = FakeProvider(BASE_BARS)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    ingest_securities(conn, provider, [(security, "TEST")], verbose=False)
    rows = conn.execute(
        "SELECT * FROM price_series_provenance WHERE security_id = ?", (security,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["provider"] == "fake"
    assert rows[0]["valid_to"] is None


def test_switching_provider_refetches_and_never_splices(conn, security):
    old = FakeProvider(BASE_BARS, name="old_vendor")
    ingest_securities(conn, old, [(security, "TEST")], verbose=False)

    new_bars = [
        PriceBar("2026-01-02", 100.2, 101.2, 99.2, 100.7, 1_000_500),
        PriceBar("2026-01-05", 100.7, 102.2, 100.2, 101.9, 1_100_500),
        PriceBar("2026-01-06", 101.9, 103.2, 101.2, 102.9, 1_200_500),
    ]
    new = FakeProvider(new_bars, name="new_vendor")
    ingest_module.switch_provider(
        conn, new, [(security, "TEST")], switch_reason="vendor evaluation"
    )

    providers = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT provider FROM prices WHERE security_id = ?", (security,)
        )
    }
    assert providers == {"new_vendor"}, "a series must never mix two providers"

    windows = conn.execute(
        "SELECT provider, valid_to, switch_reason FROM price_series_provenance "
        "WHERE security_id = ? ORDER BY valid_from",
        (security,),
    ).fetchall()
    closed = [w for w in windows if w["valid_to"] is not None]
    assert any(w["provider"] == "old_vendor" for w in closed)
    assert any(w["switch_reason"] == "vendor evaluation" for w in closed)


def test_corporate_actions_ledger_rejects_a_split_without_a_ratio(conn, security):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO corporate_actions (security_id, ex_date, action_type, provider) "
            "VALUES (?, '2026-01-05', 'split', 'test')",
            (security,),
        )


def test_corporate_actions_ledger_rejects_a_dividend_without_an_amount(conn, security):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO corporate_actions (security_id, ex_date, action_type, provider) "
            "VALUES (?, '2026-01-05', 'dividend', 'test')",
            (security,),
        )
