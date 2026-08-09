"""Fetch Wall Street analyst consensus (rating counts + price targets) for
the currently-included universe, via yfinance -- the same library and the
same "personal use" licensing posture as pipeline/prices/yfinance_provider.py.

This is third-party OPINION data. It never feeds scoring, selection or
risk_flags (nothing in those modules imports this one), and every row this
writes is a fact about what Yahoo reported at fetch time, not this system's
own view -- see migrations/025_analyst_snapshots.up.sql's header for the
same boundary the language audit already enforces for the bot's own copy.

A failed fetch for one security writes a row with fetch_error set rather
than being silently skipped -- "we tried and it failed" and "we never tried"
must stay distinguishable, same principle as risk_flags' 'unknown' severity.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from prices.yfinance_provider import YFinanceProvider  # noqa: E402
from sec.payload_store import utc_now  # noqa: E402

DEFAULT_SLEEP_SECONDS = 0.25


def included_securities(conn) -> list[dict]:
    """Every security 'included' as of the newest monthly membership snapshot.

    Same source /universe reads from: the newest snapshot_id among
    run_type='monthly_membership' runs, filtered to status='included'.
    Securities never run through a monthly snapshot (e.g. an untouched
    fixture-only row) are left out -- there is no membership decision to
    read for them yet.
    """
    rows = conn.execute(
        """
        SELECT s.security_id, s.name, l.symbol
          FROM universe_snapshots u
          JOIN securities s ON s.security_id = u.security_id
          LEFT JOIN listings l ON l.security_id = u.security_id AND l.valid_to IS NULL
         WHERE u.status = 'included'
           AND u.snapshot_id = (
               SELECT r.snapshot_id FROM universe_snapshot_runs r
                WHERE r.run_type = 'monthly_membership'
                ORDER BY r.effective_at DESC LIMIT 1
           )
           AND l.symbol IS NOT NULL
         ORDER BY s.security_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_one(provider: YFinanceProvider, symbol: str) -> dict:
    """One security's snapshot fields, or fetch_error set on failure.

    Two independent yfinance surfaces: `.info` (target prices, consensus
    mean/key, count) and `.get_recommendations()` (period-bucketed rating
    counts). Either can be present without the other, so both are attempted
    and stored independently rather than one failure discarding both.
    """
    yahoo_symbol = provider.provider_symbol(symbol)
    import yfinance as yf

    ticker = yf.Ticker(yahoo_symbol)
    result: dict = {
        "currency": None, "num_analysts": None,
        "target_low": None, "target_mean": None, "target_median": None, "target_high": None,
        "recommendation_key": None, "recommendation_mean": None,
        "rating_strong_buy": None, "rating_buy": None, "rating_hold": None,
        "rating_sell": None, "rating_strong_sell": None,
        "fetch_error": None,
    }

    info_error = None
    try:
        info = ticker.info or {}
        result["currency"] = info.get("currency")
        result["num_analysts"] = info.get("numberOfAnalystOpinions")
        result["target_low"] = info.get("targetLowPrice")
        result["target_mean"] = info.get("targetMeanPrice")
        result["target_median"] = info.get("targetMedianPrice")
        result["target_high"] = info.get("targetHighPrice")
        result["recommendation_key"] = info.get("recommendationKey")
        result["recommendation_mean"] = info.get("recommendationMean")
    except Exception as exc:  # noqa: BLE001
        info_error = str(exc)

    rec_error = None
    try:
        summary = ticker.recommendations_summary
        if summary is not None and not summary.empty:
            current = summary[summary["period"] == "0m"]
            if not current.empty:
                row = current.iloc[0]
                result["rating_strong_buy"] = int(row.get("strongBuy", 0) or 0)
                result["rating_buy"] = int(row.get("buy", 0) or 0)
                result["rating_hold"] = int(row.get("hold", 0) or 0)
                result["rating_sell"] = int(row.get("sell", 0) or 0)
                result["rating_strong_sell"] = int(row.get("strongSell", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        rec_error = str(exc)

    no_target = result["num_analysts"] is None and result["target_mean"] is None
    no_ratings = result["rating_buy"] is None and result["rating_hold"] is None
    if no_target and no_ratings:
        result["fetch_error"] = "; ".join(filter(None, [info_error, rec_error])) or "no data returned"

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest analyst consensus snapshots (yfinance)")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--symbols", nargs="*", help="restrict to these tickers")
    parser.add_argument("--limit", type=int, default=None, help="stop after N securities")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    args = parser.parse_args(argv)

    conn = migrate.connect(Path(args.db))
    provider = YFinanceProvider()
    fetched_at = utc_now()

    securities = included_securities(conn)
    if args.symbols:
        wanted = {s.upper() for s in args.symbols}
        securities = [s for s in securities if s["symbol"].upper() in wanted]
    if args.limit:
        securities = securities[: args.limit]

    ok = 0
    failed = 0
    for i, security in enumerate(securities):
        try:
            data = fetch_one(provider, security["symbol"])
        except Exception as exc:  # noqa: BLE001
            data = {
                "currency": None, "num_analysts": None, "target_low": None,
                "target_mean": None, "target_median": None, "target_high": None,
                "recommendation_key": None, "recommendation_mean": None,
                "rating_strong_buy": None, "rating_buy": None, "rating_hold": None,
                "rating_sell": None, "rating_strong_sell": None,
                "fetch_error": str(exc),
            }

        if data["fetch_error"]:
            failed += 1
            print(f"{security['symbol']:<8} FAILED: {data['fetch_error']}", file=sys.stderr)
        else:
            ok += 1
            print(
                f"{security['symbol']:<8} analysts={data['num_analysts'] or 0:<4} "
                f"target_mean={data['target_mean']} rec={data['recommendation_key']}"
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO analyst_snapshots
                (security_id, fetched_at, source, currency, num_analysts,
                 target_low, target_mean, target_median, target_high,
                 recommendation_key, recommendation_mean,
                 rating_strong_buy, rating_buy, rating_hold, rating_sell, rating_strong_sell,
                 fetch_error)
            VALUES (?, ?, 'yfinance', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                security["security_id"], fetched_at, data["currency"], data["num_analysts"],
                data["target_low"], data["target_mean"], data["target_median"], data["target_high"],
                data["recommendation_key"], data["recommendation_mean"],
                data["rating_strong_buy"], data["rating_buy"], data["rating_hold"],
                data["rating_sell"], data["rating_strong_sell"], data["fetch_error"],
            ),
        )
        conn.commit()

        if i < len(securities) - 1:
            time.sleep(args.sleep)

    conn.close()
    print(f"\n=== analyst ingest summary === ok={ok} failed={failed} total={len(securities)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
