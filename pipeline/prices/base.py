"""The price provider interface.

Every piece of price data in this system comes through a PriceProvider. Nothing
else imports a vendor library. To change vendor you write one new provider class
and change one line in registry.py.

Two rules the interface exists to protect:

  * RAW ONLY. A provider returns raw traded OHLCV. It must never return
    split- or dividend-adjusted prices, because adjustment is computed at read
    time from the corporate actions ledger.

  * NO SPLICING. A security's series comes from exactly one provider at a time.
    Switching providers means refetching that security's entire history and
    recording the switch in price_series_provenance. Stitching a new provider
    onto the end of an old series is forbidden: the two vendors disagree about
    old prices, and the seam looks like a real return.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PriceBar:
    """One raw daily bar. `date` is the US market trading date (ET), YYYY-MM-DD."""

    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None

    def values(self) -> tuple[float | None, float | None, float | None, float | None, int | None]:
        return (self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True)
class CorporateActionRecord:
    """One corporate action. `ex_date` is the ET trading date it takes effect.

    For a split, `ratio` is new shares per old share: 10.0 means a 10-for-1
    split, 0.05 means a 1-for-20 reverse split.
    """

    ex_date: str
    action_type: str
    ratio: float | None = None
    cash_amount: float | None = None
    requires_manual_review: bool = False


class PriceProviderError(RuntimeError):
    pass


class PriceProvider(ABC):
    """Implement this to add a vendor. Nothing else needs to change."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier stored on every price row, e.g. 'yfinance'."""

    @abstractmethod
    def provider_symbol(self, exchange_symbol: str) -> str:
        """Translate our canonical exchange symbol into the vendor's spelling.

        Vendors disagree about punctuation. Nasdaq Trader writes BRK.B, the SEC
        writes BRK-B, Yahoo writes BRK-B. This is where that is handled, per
        vendor, instead of leaking into the ingestion code.
        """

    @abstractmethod
    def fetch_daily_bars(self, exchange_symbol: str, years: int) -> list[PriceBar]:
        """Raw, unadjusted daily bars. Empty list when the vendor has nothing."""

    @abstractmethod
    def fetch_corporate_actions(
        self, exchange_symbol: str, years: int
    ) -> list[CorporateActionRecord]:
        """Splits and cash dividends over the same window."""
