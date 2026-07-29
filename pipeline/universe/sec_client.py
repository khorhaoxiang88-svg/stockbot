"""SEC EDGAR client.

Verified against SEC guidance on 2026-07-29:
  * Requests must declare a User-Agent identifying the requester, in the form
    "Sample Company Name AdminContact@example.com".
  * The stated maximum is 10 requests per second across all machines. Exceed it
    and the IP gets throttled.

This client therefore refuses to run without SEC_USER_AGENT set, and enforces
its own rate limit below the SEC ceiling.

Endpoints used:
  https://www.sec.gov/files/company_tickers.json   ticker -> CIK map
  https://data.sec.gov/submissions/CIK##########.json  company facts incl. SIC
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# SEC ceiling is 10/s. We stay under it on purpose.
DEFAULT_MAX_REQUESTS_PER_SECOND = 8.0

# "Name email@example.com" - a contact name followed by an email address.
USER_AGENT_RE = re.compile(r"^\S.*\s+[^@\s]+@[^@\s]+\.[^@\s]+$")


class SecClientError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RateLimiter:
    """Simple thread-safe spacing limiter: at most `rate` calls per second."""

    def __init__(self, rate_per_second: float):
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._min_interval = 1.0 / rate_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last_call = now


def resolve_user_agent(explicit: str | None = None) -> str:
    """Read and validate the SEC User-Agent. Raises if unusable."""
    user_agent = explicit or os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        raise SecClientError(
            "SEC_USER_AGENT is not set. The SEC requires a declared User-Agent of the form "
            '"Your Name your.email@example.com". Add it to .env before fetching SEC data.'
        )
    if not USER_AGENT_RE.match(user_agent):
        raise SecClientError(
            f"SEC_USER_AGENT is malformed: {user_agent!r}. "
            'It must look like "Your Name your.email@example.com".'
        )
    return user_agent


class SecClient:
    def __init__(
        self,
        user_agent: str | None = None,
        rate_per_second: float | None = None,
        raw_dir: Path | None = None,
    ):
        self.user_agent = resolve_user_agent(user_agent)
        rate = rate_per_second
        if rate is None:
            rate = float(os.environ.get("SEC_MAX_RPS", DEFAULT_MAX_REQUESTS_PER_SECOND))
        if rate > 10:
            raise SecClientError(
                f"Configured rate {rate}/s exceeds the SEC limit of 10 requests per second."
            )
        self.limiter = RateLimiter(rate)
        self.raw_dir = raw_dir or RAW_DIR
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        )

    def _get(self, url: str, timeout: int = 30) -> requests.Response:
        self.limiter.acquire()
        response = self.session.get(url, timeout=timeout)
        if response.status_code == 403:
            raise SecClientError(
                f"SEC returned 403 for {url}. This usually means the User-Agent was rejected. "
                f"Current value: {self.user_agent!r}"
            )
        response.raise_for_status()
        return response

    def _save_raw(self, source: str, filename: str, content: bytes) -> Path:
        """Raw payloads land in data/raw/<source>/<UTC date>/<filename>."""
        target_dir = self.raw_dir / source / utc_today()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        path.write_bytes(content)
        return path

    def fetch_company_tickers(self) -> dict[str, dict[str, Any]]:
        """Ticker -> {cik, ticker, title}. Ticker keys are upper-cased."""
        response = self._get(COMPANY_TICKERS_URL)
        self._save_raw("sec", "company_tickers.json", response.content)
        payload = response.json()
        by_ticker: dict[str, dict[str, Any]] = {}
        for record in payload.values():
            ticker = str(record["ticker"]).upper()
            by_ticker[ticker] = {
                "cik": str(record["cik_str"]).zfill(10),
                "ticker": ticker,
                "title": record["title"],
            }
        return by_ticker

    def fetch_submissions(self, cik: str) -> dict[str, Any]:
        """Company submissions record: name, SIC, tickers, exchanges, filings."""
        cik10 = str(cik).zfill(10)
        response = self._get(SUBMISSIONS_URL.format(cik=cik10))
        self._save_raw("sec", f"submissions_CIK{cik10}.json", response.content)
        return response.json()


TAG_RE = re.compile(r"<[^>]+>")
COVER_TITLE_RE = re.compile(r"title\s+of\s+each\s+class(.{0,400})", re.I | re.S)


def sec_symbol(exchange_symbol: str) -> str:
    """Nasdaq Trader symbol -> the form used in SEC company_tickers.json.

    Verified against live data: the two sources disagree on separators.
        BRK.B  -> BRK-B      class shares use a dot vs a dash
        ABR$D  -> ABR-PD     preferred series use a dollar vs '-P'
    """
    return exchange_symbol.upper().replace("$", "-P").replace(".", "-")


class SecClientCoverPageMixin:
    """Split out only to keep the class above readable."""

    def fetch_registered_security_title(
        self: "SecClient", cik: str, max_filings: int = 3
    ) -> tuple[str | None, str | None, str | None]:
        """Read 'Title of each class' off the most recent annual report cover.

        This is the classification path for securities that have left the
        exchange directory files. The title is filed text, not an inference.
        Amendments are skipped because their cover pages often omit the table.

        Returns (title_text, form, filing_date), all None when nothing matched.
        """
        submissions = self.fetch_submissions(cik)
        recent = submissions["filings"]["recent"]
        annual = [
            (form, date, accession, document)
            for form, date, accession, document in zip(
                recent["form"],
                recent["filingDate"],
                recent["accessionNumber"],
                recent["primaryDocument"],
            )
            if form in ("10-K", "20-F", "40-F")
        ]
        for form, date, accession, document in annual[:max_filings]:
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession.replace('-', '')}/{document}"
            )
            try:
                html = self._get(url).text
            except Exception:  # noqa: BLE001
                continue
            text = TAG_RE.sub(" ", html)
            text = re.sub(r"&nbsp;?|&#160;|&#8203;", " ", text)
            text = re.sub(r"\s+", " ", text)
            match = COVER_TITLE_RE.search(text)
            if match:
                return match.group(1).strip(), form, date
        return None, None, None


# Attach the mixin method to SecClient without disturbing its constructor.
SecClient.fetch_registered_security_title = (
    SecClientCoverPageMixin.fetch_registered_security_title
)


def load_dotenv_into_environ(path: Path | None = None) -> None:
    """Load .env from the repo root. No-op if the file is absent."""
    from dotenv import load_dotenv

    load_dotenv(path or (REPO_ROOT / ".env"))


if __name__ == "__main__":
    load_dotenv_into_environ()
    client = SecClient()
    tickers = client.fetch_company_tickers()
    print(f"company_tickers.json: {len(tickers)} tickers")
    print("sample:", json.dumps(tickers.get("AAPL")))
