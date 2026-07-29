"""Nasdaq Trader symbol directory client.

Verified against the real files on 2026-07-29. Both are pipe-delimited with a
header row and a "File Creation Time:" footer row that must be dropped.

nasdaqlisted.txt columns:
  Symbol | Security Name | Market Category | Test Issue | Financial Status |
  Round Lot Size | ETF | NextShares
  (the published field-definitions page omits ETF and NextShares; the live file
  has them, so the header row is parsed rather than assumed)

otherlisted.txt columns:
  ACT Symbol | Security Name | Exchange | CQS Symbol | ETF | Round Lot Size |
  Test Issue | NASDAQ Symbol

Exchange codes in otherlisted.txt: A = NYSE American, N = NYSE, P = NYSE Arca,
Z = Cboe BZX, V = IEX.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"

BASE_URL = "https://www.nasdaqtrader.com/dynamic/SymDir"
NASDAQ_LISTED = "nasdaqlisted.txt"
OTHER_LISTED = "otherlisted.txt"

EXCHANGE_CODES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}


class NasdaqClientError(RuntimeError):
    pass


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class NasdaqClient:
    def __init__(self, user_agent: str | None = None, raw_dir: Path | None = None):
        # Nasdaq does not mandate a contact User-Agent, but sending the same one
        # we declare to the SEC keeps our traffic identifiable everywhere.
        self.user_agent = user_agent or os.environ.get("SEC_USER_AGENT") or "stockbot"
        self.raw_dir = raw_dir or RAW_DIR
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def _fetch_file(self, filename: str) -> list[str]:
        response = self.session.get(f"{BASE_URL}/{filename}", timeout=60)
        response.raise_for_status()
        target_dir = self.raw_dir / "nasdaq_trader" / utc_today()
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / filename).write_bytes(response.content)
        return response.text.splitlines()

    @staticmethod
    def _parse(lines: list[str], filename: str) -> list[dict[str, str]]:
        if not lines:
            raise NasdaqClientError(f"{filename} came back empty")
        header = lines[0].split("|")
        rows: list[dict[str, str]] = []
        for line in lines[1:]:
            if not line.strip() or line.startswith("File Creation Time:"):
                continue
            values = line.split("|")
            if len(values) != len(header):
                continue
            rows.append(dict(zip(header, values)))
        if not rows:
            raise NasdaqClientError(f"{filename} produced no usable rows")
        return rows

    def fetch_nasdaq_listed(self) -> list[dict[str, str]]:
        return self._parse(self._fetch_file(NASDAQ_LISTED), NASDAQ_LISTED)

    def fetch_other_listed(self) -> list[dict[str, str]]:
        return self._parse(self._fetch_file(OTHER_LISTED), OTHER_LISTED)

    def fetch_all_listings(self) -> dict[str, dict[str, Any]]:
        """Symbol -> normalised listing record from both directory files.

        Normalised keys: symbol, security_name, exchange, is_etf, is_test_issue,
        source_file. Nasdaq-listed symbols win over the other-listed file when a
        symbol appears in both, because the Nasdaq file is the primary listing
        record for those securities.
        """
        listings: dict[str, dict[str, Any]] = {}

        for row in self.fetch_other_listed():
            symbol = row["ACT Symbol"].strip().upper()
            listings[symbol] = {
                "symbol": symbol,
                "security_name": row["Security Name"].strip(),
                "exchange": EXCHANGE_CODES.get(row["Exchange"].strip(), row["Exchange"].strip()),
                "is_etf": row["ETF"].strip().upper() == "Y",
                "is_test_issue": row["Test Issue"].strip().upper() == "Y",
                "source_file": OTHER_LISTED,
            }

        for row in self.fetch_nasdaq_listed():
            symbol = row["Symbol"].strip().upper()
            listings[symbol] = {
                "symbol": symbol,
                "security_name": row["Security Name"].strip(),
                "exchange": "Nasdaq",
                "is_etf": row.get("ETF", "N").strip().upper() == "Y",
                "is_test_issue": row["Test Issue"].strip().upper() == "Y",
                "source_file": NASDAQ_LISTED,
            }

        return listings


if __name__ == "__main__":
    client = NasdaqClient()
    all_listings = client.fetch_all_listings()
    print(f"{len(all_listings)} symbols across both directory files")
    print("AAPL:", all_listings.get("AAPL"))
