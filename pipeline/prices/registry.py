"""The one file to edit when swapping price provider.

Change ACTIVE_PROVIDER, and every part of the system that touches prices follows.
Nothing else imports a vendor library directly.

Switching provider is NOT just a config change in practice. A provider's history
must never be spliced onto another's, so a switch means:

  1. change ACTIVE_PROVIDER here,
  2. refetch each security's full history from the new provider,
  3. close the old price_series_provenance window and open a new one with a
     switch_reason.

`ingest.switch_provider()` performs steps 2 and 3.
"""

from __future__ import annotations

from prices.base import PriceProvider
from prices.yfinance_provider import YFinanceProvider

# ---------------------------------------------------------------------------
# The active provider for Release 1.
ACTIVE_PROVIDER: type[PriceProvider] = YFinanceProvider
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[PriceProvider]] = {
    "yfinance": YFinanceProvider,
}


def get_provider(name: str | None = None) -> PriceProvider:
    """Return the active provider, or a named one for tests and migrations."""
    if name is None:
        return ACTIVE_PROVIDER()
    if name not in _PROVIDERS:
        known = ", ".join(sorted(_PROVIDERS))
        raise KeyError(f"Unknown price provider {name!r}. Known providers: {known}")
    return _PROVIDERS[name]()


def register_provider(name: str, provider_class: type[PriceProvider]) -> None:
    """Register an additional provider, used by tests to inject fakes."""
    _PROVIDERS[name] = provider_class
