"""A spin-off ratio must never reach the price-adjustment logic.

Guards the whole chain, not just the provider: even if a 'spinoff' or 'other'
row lands in the ledger with a ratio, adjusted_series must ignore it.
"""

import pytest

import migrate
from prices.adjust import adjusted_series, return_across_date
from prices.base import CorporateActionRecord, PriceBar
from prices.ingest import ingest_securities
from tests.test_prices import FakeProvider  # reuse the offline provider
from universe import identity

# Honeywell-shaped: a 6% drop on the spin-off date, factor 1.061.
SPINOFF_BARS = [
    PriceBar("2025-10-28", 210.0, 213.0, 209.0, 212.00, 3_000_000),
    PriceBar("2025-10-29", 211.0, 214.0, 210.0, 212.89, 3_100_000),
    PriceBar("2025-10-30", 200.0, 203.0, 199.0, 200.11, 9_000_000),
    PriceBar("2025-10-31", 201.0, 204.0, 200.0, 202.00, 4_000_000),
]


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "spinoff.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


@pytest.fixture
def security(conn):
    security_id = identity.create_security(
        conn,
        name="Honeywell-like Common Stock",
        cik="0000773840",
        security_type="common_stock",
        classification_confidence="high",
        classification_source="test",
        first_seen="2025-01-01T00:00:00Z",
        last_seen="2025-01-01T00:00:00Z",
    )
    identity.add_listing(
        conn, security_id=security_id, symbol="HONX", exchange="Nasdaq", valid_from="2020-01-01"
    )
    return security_id


@pytest.mark.parametrize("action_type", ["other", "spinoff"])
def test_non_split_ratio_rows_do_not_adjust_prices(conn, security, action_type):
    action = CorporateActionRecord(
        ex_date="2025-10-30",
        action_type=action_type,
        ratio=1.061,
        requires_manual_review=True,
    )
    ingest_securities(
        conn, FakeProvider(SPINOFF_BARS, [action]), [(security, "HONX")], verbose=False
    )

    bars = adjusted_series(conn, security)
    assert {bar.factor for bar in bars} == {1.0}, "a spin-off must not rescale history"

    raw_move = 200.11 / 212.89 - 1
    adjusted_move = return_across_date(bars, "2025-10-30")
    assert adjusted_move == pytest.approx(raw_move, abs=1e-6)


def test_split_row_flagged_for_review_also_does_not_adjust(conn, security):
    action = CorporateActionRecord(
        ex_date="2025-10-30", action_type="split", ratio=1.061, requires_manual_review=True
    )
    ingest_securities(
        conn, FakeProvider(SPINOFF_BARS, [action]), [(security, "HONX")], verbose=False
    )
    assert {bar.factor for bar in adjusted_series(conn, security)} == {1.0}


def test_a_real_split_still_adjusts(conn, security):
    """The guard must not become a blanket refusal to adjust anything."""
    bars = [
        PriceBar("2024-06-07", 1205.0, 1215.0, 1200.0, 1208.0, 41_000_000),
        PriceBar("2024-06-10", 120.0, 122.5, 119.0, 121.8, 313_000_000),
    ]
    action = CorporateActionRecord(ex_date="2024-06-10", action_type="split", ratio=10.0)
    ingest_securities(conn, FakeProvider(bars, [action]), [(security, "HONX")], verbose=False)

    adjusted = adjusted_series(conn, security)
    assert {bar.factor for bar in adjusted} == {10.0, 1.0}
    assert abs(return_across_date(adjusted, "2024-06-10")) < 0.05


def test_two_share_classes_with_different_ratios_are_both_quarantined(conn):
    """Lennar's case: LEN got 1.033 and LEN.B got 1.052 on the same day.

    A real split applies identically to every class of the same issuer, so
    different ratios on one date are proof this is not a split.
    """
    ids = []
    for symbol, ratio in (("LENX", 1.033), ("LENX.B", 1.052)):
        security_id = identity.create_security(
            conn,
            name=f"Lennar-like {symbol}",
            cik="0000920760",
            share_class="A" if symbol == "LENX" else "B",
            security_type="common_stock",
            classification_confidence="high",
            classification_source="test",
            first_seen="2025-01-01T00:00:00Z",
            last_seen="2025-01-01T00:00:00Z",
        )
        identity.add_listing(
            conn, security_id=security_id, symbol=symbol, exchange="NYSE", valid_from="2020-01-01"
        )
        action = CorporateActionRecord(
            ex_date="2025-10-30", action_type="other", ratio=ratio, requires_manual_review=True
        )
        ingest_securities(
            conn, FakeProvider(SPINOFF_BARS, [action]), [(security_id, symbol)], verbose=False
        )
        ids.append(security_id)

    for security_id in ids:
        assert {bar.factor for bar in adjusted_series(conn, security_id)} == {1.0}
