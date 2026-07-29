"""yfinance price provider.

Release 1 prototype source. Verified against yfinance 1.5.2 on 2026-07-29:

  * Ticker.history(auto_adjust=False) returns Open/High/Low/Close plus a
    separate 'Adj Close' column, which we discard. auto_adjust must be passed
    explicitly - recent yfinance versions default it to True.

  * IMPORTANT: auto_adjust=False only turns off DIVIDEND adjustment. The OHLC
    that comes back is still SPLIT-adjusted by Yahoo at source. Verified on
    2026-07-29: Yahoo reports NVDA's 2024-06-07 close as 120.89, but NVDA
    actually traded near 1208.90 that day and did not split until 2024-06-10.
    Yahoo has divided every pre-split bar by 10.

    That breaks the raw-only rule, and worse, it double-adjusts: applying our
    own split adjustment on top produced a +907% artificial return across the
    ex-date. So this provider UN-ADJUSTS before returning, multiplying each bar
    by the cumulative ratio of all splits after it and dividing volume by the
    same factor. What leaves this class is genuine traded price.
  * The returned index is timezone-aware in America/New_York, so the calendar
    date of each bar is already the US trading date.
  * Splits arrive in the 'Stock Splits' column and via Ticker.splits, as new
    shares per old share. Dividends arrive in 'Dividends' and Ticker.dividends,
    as cash per share.

Licensing note: yfinance is not affiliated with or endorsed by Yahoo, and the
Yahoo Finance API is documented as being for personal use. This provider is
therefore a private, non-commercial prototype source only, which is exactly why
everything sits behind the PriceProvider interface.
"""

from __future__ import annotations

import math
from fractions import Fraction

import yfinance as yf

from prices.base import CorporateActionRecord, PriceBar, PriceProvider, PriceProviderError

# A split ratio outside this range is almost certainly a vendor error rather
# than a real action, so it gets flagged for a human instead of being trusted.
MIN_PLAUSIBLE_RATIO = 0.001
MAX_PLAUSIBLE_RATIO = 1000.0

# Largest denominator a genuine split ratio is expected to need. A 3-for-2 or
# 5-for-4 split is normal; a ratio needing a denominator of 30 is not a split.
MAX_SPLIT_DENOMINATOR = 20
RATIO_TOLERANCE = 1e-6


def is_clean_split_ratio(ratio: float, tolerance: float = RATIO_TOLERANCE) -> bool:
    """Does this ratio look like a real stock split?

    Real splits are announced as small whole-number ratios: 10-for-1, 3-for-2,
    1-for-50. Those are exactly representable as a fraction with a small
    denominator (checking the reciprocal too, so reverse splits pass).

    Spin-off adjustment factors are not: Honeywell's Solstice spin-off arrives
    from Yahoo as 1.061, and its Aerospace spin-off as 0.9535. Neither is any
    sensible split ratio, and treating them as splits silently rewrites price
    history for an event with completely different economics.
    """
    if ratio is None or ratio <= 0 or math.isnan(ratio):
        return False
    candidates = [ratio]
    if ratio < 1:
        candidates.append(1.0 / ratio)
    for value in candidates:
        approximation = Fraction(value).limit_denominator(MAX_SPLIT_DENOMINATOR)
        if abs(float(approximation) - value) <= tolerance:
            return True
    return False


class YFinanceProvider(PriceProvider):
    @property
    def name(self) -> str:
        return "yfinance"

    def provider_symbol(self, exchange_symbol: str) -> str:
        """Nasdaq Trader spelling -> Yahoo spelling.

        BRK.B -> BRK-B, ABR$D -> ABR-PD. Same shape as the SEC mapping, but kept
        separate because these are two independent vendors that happen to agree.
        """
        return exchange_symbol.upper().replace("$", "-P").replace(".", "-")

    def _history(self, exchange_symbol: str, years: int):
        symbol = self.provider_symbol(exchange_symbol)
        try:
            ticker = yf.Ticker(symbol)
            # auto_adjust=False is mandatory here. See the module docstring.
            frame = ticker.history(period=f"{years}y", auto_adjust=False, actions=True)
        except Exception as exc:  # noqa: BLE001
            raise PriceProviderError(f"yfinance failed for {symbol}: {exc}") from exc
        return frame

    @staticmethod
    def _splits_in_frame(frame) -> list[tuple[str, float]]:
        """(ex_date, ratio) pairs from the frame's own Stock Splits column.

        This deliberately takes EVERY non-zero factor, including the spin-off
        factors that fetch_corporate_actions refuses to file as splits. The two
        jobs are different: Yahoo divided its price history by all of these, so
        undoing all of them is what recovers the traded price. Filtering here
        would leave the spin-off securities permanently mis-scaled.
        """
        splits: list[tuple[str, float]] = []
        if "Stock Splits" not in frame.columns:
            return splits
        for timestamp, value in frame["Stock Splits"].items():
            try:
                ratio = float(value)
            except (TypeError, ValueError):
                continue
            if ratio and not math.isnan(ratio) and ratio != 0.0:
                splits.append((timestamp.date().isoformat(), ratio))
        return splits

    @staticmethod
    def _unadjust_factor(splits: list[tuple[str, float]], date: str) -> float:
        """Cumulative ratio of every split AFTER `date`.

        Yahoo divided this bar by exactly this factor, so multiplying by it
        recovers the price the security actually traded at.
        """
        factor = 1.0
        for ex_date, ratio in splits:
            if ex_date > date:
                factor *= ratio
        return factor

    def fetch_daily_bars(self, exchange_symbol: str, years: int = 3) -> list[PriceBar]:
        frame = self._history(exchange_symbol, years)
        if frame is None or frame.empty:
            return []

        splits = self._splits_in_frame(frame)

        bars: list[PriceBar] = []
        for timestamp, row in frame.iterrows():
            # The index is already Eastern, so .date() is the trading date.
            trading_date = timestamp.date().isoformat()

            # Undo Yahoo's split adjustment to recover the traded price.
            factor = self._unadjust_factor(splits, trading_date)

            def clean(value, scale=factor):
                if value is None:
                    return None
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return None
                return None if math.isnan(number) else round(number * scale, 6)

            volume = row.get("Volume")
            try:
                if volume is None or math.isnan(float(volume)):
                    volume = None
                else:
                    # Volume is adjusted the opposite way to price.
                    volume = int(round(float(volume) / factor))
            except (TypeError, ValueError):
                volume = None

            bars.append(
                PriceBar(
                    date=trading_date,
                    open=clean(row.get("Open")),
                    high=clean(row.get("High")),
                    low=clean(row.get("Low")),
                    close=clean(row.get("Close")),
                    volume=volume,
                )
            )
        return bars

    def fetch_corporate_actions(
        self, exchange_symbol: str, years: int = 3
    ) -> list[CorporateActionRecord]:
        frame = self._history(exchange_symbol, years)
        if frame is None or frame.empty:
            return []

        actions: list[CorporateActionRecord] = []
        for timestamp, row in frame.iterrows():
            ex_date = timestamp.date().isoformat()

            split = row.get("Stock Splits")
            try:
                split = float(split) if split is not None else 0.0
            except (TypeError, ValueError):
                split = 0.0
            if split and not math.isnan(split) and split != 0.0:
                implausible = not (MIN_PLAUSIBLE_RATIO <= split <= MAX_PLAUSIBLE_RATIO)
                clean = is_clean_split_ratio(split)
                if clean and not implausible:
                    actions.append(
                        CorporateActionRecord(
                            ex_date=ex_date,
                            action_type="split",
                            ratio=round(split, 8),
                            requires_manual_review=False,
                        )
                    )
                else:
                    # Yahoo packs spin-off adjustment factors into the same
                    # "Stock Splits" column as real splits, and exposes nothing
                    # that tells them apart: Ticker.actions has only Dividends
                    # and Stock Splits. Verified against SEC filings on
                    # 2026-07-29 - Honeywell's 1.061 (2025-10-30, Solstice
                    # Advanced Materials) and 0.9535 (2026-06-29, Honeywell
                    # Aerospace) are both completed spin-offs, and Lennar's
                    # 1.033 (2025-01-21) is the Millrose Properties spin-off.
                    #
                    # So this records what is actually known - a ratio-bearing
                    # corporate action of undetermined type - rather than
                    # asserting "split". 'other' plus requires_manual_review
                    # keeps it out of the adjustment logic twice over, since
                    # adjust.py takes only action_type='split' with the review
                    # flag clear. A human confirms the type and reclassifies.
                    actions.append(
                        CorporateActionRecord(
                            ex_date=ex_date,
                            action_type="other",
                            ratio=round(split, 8),
                            requires_manual_review=True,
                        )
                    )

            dividend = row.get("Dividends")
            try:
                dividend = float(dividend) if dividend is not None else 0.0
            except (TypeError, ValueError):
                dividend = 0.0
            if dividend and not math.isnan(dividend) and dividend != 0.0:
                actions.append(
                    CorporateActionRecord(
                        ex_date=ex_date,
                        action_type="dividend",
                        cash_amount=round(dividend, 8),
                        requires_manual_review=dividend < 0,
                    )
                )
        return actions
