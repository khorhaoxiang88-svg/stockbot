"""Momentum inputs, computed from the SPLIT-ADJUSTED series only.

Raw prices cross a split with a 90% gap in them. Every quantity here is a ratio
of two prices, so a split inside the lookback would dominate the signal. The
adjusted series comes from pipeline/prices/adjust.py, which rebuilds it at read
time from the corporate actions ledger; nothing stored is adjusted.

Momentum is ranked against the WHOLE operating universe rather than the cohort.
Relative strength is a statement about where capital is going across the market,
and a cohort-relative version of it would score the best-performing name in a
collapsing industry as though it were a leader.

Weights sum to exactly 1.00 and there is NO renormalisation. If any input is
missing the gate has failed and momentum is NULL. Filling the hole by growing
the other weights would silently change what the number means.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_TRADING_DAYS = 250

# Nominal weights, frozen. These must sum to 1.0; asserted at import.
WEIGHTS: dict[str, float] = {
    "rs_21": 0.15,
    "rs_63": 0.25,
    "rs_126": 0.25,
    "rs_252": 0.15,
    "range52": 0.10,
    "trend": 0.05,
    "volratio": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-12

RS_LOOKBACKS = (21, 63, 126, 252)
RANGE_WINDOW = 252
PERCENTILE_RANKED = ("rs_21", "rs_63", "rs_126", "rs_252", "volratio")
USED_DIRECTLY = ("range52", "trend")


@dataclass(frozen=True)
class MomentumInputs:
    """Raw momentum quantities for one security. None means not computable."""

    security_id: int
    bar_count: int
    as_of_bar_date: str | None
    values: dict[str, float | None]
    detail: dict
    gate_reason: str | None

    @property
    def gate_passed(self) -> bool:
        return self.gate_reason is None


def _closes_and_volumes(bars) -> tuple[list[str], list[float], list[float]]:
    dates, closes, volumes = [], [], []
    for bar in bars:
        if bar.close is None:
            continue
        dates.append(bar.date)
        closes.append(float(bar.close))
        volumes.append(0.0 if bar.volume is None else float(bar.volume))
    return dates, closes, volumes


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _close_on_or_before(dates: list[str], closes: list[float], date: str) -> float | None:
    """SPY's close on `date`, or the most recent one before it.

    A security can trade on a session where the benchmark has no bar in this
    fixture (halts, vendor gaps). Falling back to the previous session compares
    the same two calendar points rather than silently shifting the window.
    """
    found = None
    for index, day in enumerate(dates):
        if day <= date:
            found = closes[index]
        else:
            break
    return found


def compute_inputs(
    bars, benchmark_bars, security_id: int, as_of_date: str
) -> MomentumInputs:
    """Every momentum quantity for one security, as of `as_of_date`."""
    dates, closes, volumes = _closes_and_volumes(
        [bar for bar in bars if bar.date <= as_of_date]
    )
    bench_dates, bench_closes, _ = _closes_and_volumes(
        [bar for bar in benchmark_bars if bar.date <= as_of_date]
    )

    detail: dict = {"bar_count": len(closes), "min_trading_days": MIN_TRADING_DAYS}
    values: dict[str, float | None] = {key: None for key in WEIGHTS}

    if len(closes) < MIN_TRADING_DAYS:
        return MomentumInputs(
            security_id, len(closes), dates[-1] if dates else None, values, detail,
            f"only {len(closes)} adjusted trading days, gate requires {MIN_TRADING_DAYS}",
        )
    if not bench_closes:
        return MomentumInputs(
            security_id, len(closes), dates[-1], values, detail,
            "no benchmark (SPY) bars available",
        )

    price_t = closes[-1]
    date_t = dates[-1]
    bench_t = _close_on_or_before(bench_dates, bench_closes, date_t)
    detail["as_of_bar_date"] = date_t
    detail["price_t"] = price_t
    detail["benchmark_t"] = bench_t
    detail["rs"] = {}

    missing: list[str] = []
    for lookback in RS_LOOKBACKS:
        key = f"rs_{lookback}"
        if len(closes) <= lookback or bench_t is None:
            missing.append(key)
            detail["rs"][key] = {"reason": "insufficient history for the lookback"}
            continue
        price_prior = closes[-1 - lookback]
        date_prior = dates[-1 - lookback]
        bench_prior = _close_on_or_before(bench_dates, bench_closes, date_prior)
        if not price_prior or not bench_prior or not bench_t:
            missing.append(key)
            detail["rs"][key] = {"reason": "benchmark price unavailable at the lookback date"}
            continue
        values[key] = (price_t / price_prior) / (bench_t / bench_prior) - 1.0
        detail["rs"][key] = {
            "prior_date": date_prior,
            "price_prior": price_prior,
            "benchmark_prior": bench_prior,
            "formula": "(P_t / P_prior) / (SPY_t / SPY_prior) - 1",
            "value": values[key],
        }

    # 52-week range position, used directly as a 0-100 number.
    window = closes[-RANGE_WINDOW:]
    high, low = max(window), min(window)
    if len(closes) < RANGE_WINDOW:
        missing.append("range52")
        detail["range52"] = {"reason": f"needs {RANGE_WINDOW} bars, has {len(closes)}"}
    elif high == low:
        missing.append("range52")
        detail["range52"] = {"reason": "52-week high equals low; position undefined"}
    else:
        values["range52"] = 100.0 * (price_t - low) / (high - low)
        detail["range52"] = {
            "high_252": high, "low_252": low, "price_t": price_t,
            "formula": "100 * (P_t - L252) / (H252 - L252)", "value": values["range52"],
        }

    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)
    if sma50 is None or sma200 is None:
        missing.append("trend")
        detail["trend"] = {"reason": "insufficient history for SMA50/SMA200"}
    else:
        above_sma50 = price_t > sma50
        stacked = sma50 > sma200
        values["trend"] = 100.0 if (above_sma50 and stacked) else (
            50.0 if (above_sma50 or stacked) else 0.0
        )
        detail["trend"] = {
            "sma50": sma50, "sma200": sma200, "close_above_sma50": above_sma50,
            "sma50_above_sma200": stacked, "value": values["trend"],
        }

    adv20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    adv90 = sum(volumes[-90:]) / 90 if len(volumes) >= 90 else None
    if not adv20 or not adv90:
        missing.append("volratio")
        detail["volratio"] = {"reason": "insufficient or zero volume history"}
    else:
        values["volratio"] = adv20 / adv90
        detail["volratio"] = {
            "adv20_shares_split_adjusted": adv20,
            "adv90_shares_split_adjusted": adv90,
            "value": values["volratio"],
        }

    gate_reason = None
    if missing:
        # No renormalisation is permitted, so a missing input is fatal to the
        # whole component rather than absorbed by the remaining weights.
        gate_reason = "momentum inputs unavailable: " + ", ".join(sorted(missing))

    return MomentumInputs(security_id, len(closes), date_t, values, detail, gate_reason)
