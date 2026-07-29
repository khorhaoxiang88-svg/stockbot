"""Guards the vendor quirk that caused the worst bug in this phase.

yfinance's auto_adjust=False disables DIVIDEND adjustment only. The OHLC it
returns is still split-adjusted by Yahoo. The provider un-adjusts it. These
tests run offline against a hand-built frame, so they keep working regardless of
what Yahoo is serving today.
"""

import pandas as pd
import pytest

from prices.yfinance_provider import YFinanceProvider, is_clean_split_ratio


def make_frame():
    """Split-adjusted data, exactly as Yahoo hands it over.

    A 10-for-1 split on 2024-06-10. Yahoo has already divided every earlier bar
    by 10, so the pre-split close reads 120.888 instead of the 1208.88 the stock
    actually traded at.
    """
    index = pd.to_datetime(
        ["2024-06-06", "2024-06-07", "2024-06-10", "2024-06-11"]
    ).tz_localize("America/New_York")
    return pd.DataFrame(
        {
            "Open": [120.0, 120.5, 120.0, 121.8],
            "High": [121.5, 121.5, 122.5, 124.0],
            "Low": [119.5, 120.0, 119.0, 121.0],
            "Close": [120.5, 120.888, 121.79, 123.0],
            "Adj Close": [120.4, 120.8, 121.7, 122.9],
            "Volume": [410_000_000, 412_386_000, 313_434_100, 300_000_000],
            "Dividends": [0.0, 0.0, 0.0, 0.0],
            "Stock Splits": [0.0, 0.0, 10.0, 0.0],
        },
        index=index,
    )


@pytest.fixture
def provider(monkeypatch):
    instance = YFinanceProvider()
    monkeypatch.setattr(instance, "_history", lambda symbol, years: make_frame())
    return instance


def test_prices_are_unadjusted_back_to_traded_values(provider):
    bars = {bar.date: bar for bar in provider.fetch_daily_bars("NVDA", 3)}

    # Pre-split bars are multiplied back up by the split ratio.
    assert bars["2024-06-07"].close == pytest.approx(1208.88, abs=1e-6)
    assert bars["2024-06-06"].close == pytest.approx(1205.0, abs=1e-6)
    # Bars on and after the ex-date are untouched.
    assert bars["2024-06-10"].close == pytest.approx(121.79, abs=1e-6)
    assert bars["2024-06-11"].close == pytest.approx(123.0, abs=1e-6)


def test_volume_is_unadjusted_the_opposite_way(provider):
    bars = {bar.date: bar for bar in provider.fetch_daily_bars("NVDA", 3)}
    # Yahoo reported 412,386,000 split-adjusted shares; really 41,238,600 traded.
    assert bars["2024-06-07"].volume == 41_238_600
    assert bars["2024-06-10"].volume == 313_434_100


def test_raw_series_keeps_the_real_split_cliff(provider):
    bars = {bar.date: bar for bar in provider.fetch_daily_bars("NVDA", 3)}
    raw_return = bars["2024-06-10"].close / bars["2024-06-07"].close - 1
    # Raw means raw: the ~90% drop is real and must survive ingestion.
    assert raw_return == pytest.approx(-0.8993, abs=0.001)


def test_ohlc_all_scale_together(provider):
    bars = {bar.date: bar for bar in provider.fetch_daily_bars("NVDA", 3)}
    bar = bars["2024-06-07"]
    assert bar.open == pytest.approx(1205.0, abs=1e-6)
    assert bar.high == pytest.approx(1215.0, abs=1e-6)
    assert bar.low == pytest.approx(1200.0, abs=1e-6)


def test_split_is_reported_in_corporate_actions(provider):
    actions = provider.fetch_corporate_actions("NVDA", 3)
    splits = [a for a in actions if a.action_type == "split"]
    assert len(splits) == 1
    assert splits[0].ex_date == "2024-06-10"
    assert splits[0].ratio == 10.0
    assert splits[0].requires_manual_review is False


def test_no_split_means_no_rescaling(monkeypatch):
    frame = make_frame()
    frame["Stock Splits"] = 0.0
    instance = YFinanceProvider()
    monkeypatch.setattr(instance, "_history", lambda symbol, years: frame)
    bars = {bar.date: bar for bar in instance.fetch_daily_bars("AAPL", 3)}
    assert bars["2024-06-07"].close == pytest.approx(120.888, abs=1e-6)


def test_adjusted_close_column_is_discarded(provider):
    bars = provider.fetch_daily_bars("NVDA", 3)
    assert all(not hasattr(bar, "adj_close") for bar in bars)
    assert {f for f in bars[0].__dataclass_fields__} == {
        "date", "open", "high", "low", "close", "volume"
    }


def test_symbol_translation_to_yahoo_spelling():
    provider = YFinanceProvider()
    assert provider.provider_symbol("BRK.B") == "BRK-B"
    assert provider.provider_symbol("ABR$D") == "ABR-PD"
    assert provider.provider_symbol("aapl") == "AAPL"


def test_implausible_ratio_is_not_filed_as_a_split(monkeypatch):
    frame = make_frame()
    frame.loc[frame.index[2], "Stock Splits"] = 100000.0
    instance = YFinanceProvider()
    monkeypatch.setattr(instance, "_history", lambda symbol, years: frame)
    actions = instance.fetch_corporate_actions("X", 3)
    assert [a.action_type for a in actions] == ["other"]
    assert actions[0].requires_manual_review is True


# ------------------------------------------------- splits vs spin-off factors
#
# Yahoo packs spin-off adjustment factors into the same "Stock Splits" column
# as real splits and exposes nothing that distinguishes them. Confirmed against
# SEC filings on 2026-07-29:
#   HON 1.061 on 2025-10-30  -> Solstice Advanced Materials spin-off (8-K)
#   HON 0.9535 on 2026-06-29 -> Honeywell Aerospace spin-off (8-K)
#   LEN 1.033 on 2025-01-21  -> Millrose Properties spin-off (8-K)


@pytest.mark.parametrize(
    "ratio",
    [10.0, 3.0, 2.0, 50.0, 15.0, 4.0, 1.5, 1.25, 0.1, 0.05, 0.02, 0.04, 0.125],
)
def test_real_split_ratios_are_recognised(ratio):
    assert is_clean_split_ratio(ratio) is True


@pytest.mark.parametrize("ratio", [1.061, 0.9535, 1.033, 1.052, 1.011, 1.032])
def test_spinoff_factors_are_not_recognised_as_splits(ratio):
    assert is_clean_split_ratio(ratio) is False


@pytest.mark.parametrize("ratio", [0.0, -1.0])
def test_nonsense_ratios_are_not_clean(ratio):
    assert is_clean_split_ratio(ratio) is False


def spinoff_frame(ratio: float):
    index = pd.to_datetime(
        ["2025-10-28", "2025-10-29", "2025-10-30", "2025-10-31"]
    ).tz_localize("America/New_York")
    return pd.DataFrame(
        {
            "Open": [210.0, 211.0, 200.0, 201.0],
            "High": [213.0, 214.0, 203.0, 204.0],
            "Low": [209.0, 210.0, 199.0, 200.0],
            "Close": [212.0, 212.89, 200.11, 202.0],
            "Adj Close": [211.0, 211.9, 199.1, 201.0],
            "Volume": [3_000_000, 3_100_000, 9_000_000, 4_000_000],
            "Dividends": [0.0, 0.0, 0.0, 0.0],
            "Stock Splits": [0.0, 0.0, ratio, 0.0],
        },
        index=index,
    )


@pytest.mark.parametrize("ratio", [1.061, 0.9535])
def test_spinoff_factor_is_filed_as_other_and_flagged(monkeypatch, ratio):
    instance = YFinanceProvider()
    monkeypatch.setattr(instance, "_history", lambda symbol, years: spinoff_frame(ratio))
    actions = instance.fetch_corporate_actions("HON", 3)
    ratio_actions = [a for a in actions if a.ratio is not None]

    assert len(ratio_actions) == 1
    action = ratio_actions[0]
    assert action.action_type == "other", "a spin-off must never be filed as a split"
    assert action.requires_manual_review is True
    assert action.ratio == pytest.approx(ratio)
    assert not any(a.action_type == "split" for a in actions)


@pytest.mark.parametrize("ratio", [1.061, 0.9535])
def test_spinoff_factor_is_still_used_to_recover_traded_price(monkeypatch, ratio):
    """Un-adjusting and classifying are different jobs.

    Yahoo scaled its price history by the spin-off factor, so undoing it is
    still required to recover the traded price. Only the LEDGER entry changes.
    """
    instance = YFinanceProvider()
    monkeypatch.setattr(instance, "_history", lambda symbol, years: spinoff_frame(ratio))
    bars = {bar.date: bar for bar in instance.fetch_daily_bars("HON", 3)}

    assert bars["2025-10-29"].close == pytest.approx(212.89 * ratio, abs=1e-6)
    assert bars["2025-10-30"].close == pytest.approx(200.11, abs=1e-6)


def test_a_real_split_in_the_same_shape_is_still_filed_as_a_split(monkeypatch):
    instance = YFinanceProvider()
    monkeypatch.setattr(instance, "_history", lambda symbol, years: spinoff_frame(2.0))
    actions = instance.fetch_corporate_actions("X", 3)
    assert [a.action_type for a in actions] == ["split"]
    assert actions[0].requires_manual_review is False
