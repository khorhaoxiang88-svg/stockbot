"""R1-PROTOCOL-1.1: the frozen execution rules, as pure functions.

Every ambiguity in an execution protocol is a place where paper results quietly
become fiction, so each one below has exactly one answer and that answer is
written down here rather than implied by the order of some `if` statements.

Three of them are worth stating plainly, because each one flatters a backtest if
you get it wrong:

  SLIPPAGE IS ADVERSE ON EVERY FILL. Entry pays up, every exit gets less. There
  is no path through this module that produces a favourable or free fill, and
  the database CHECK on slippage_bps refuses a non-positive value as well.

  A SPLIT IS NOT A GAP. On an ex-date the prior close is on the old basis and
  the open is on the new one. Comparing them raw makes a 10-for-1 split look
  like a 90% collapse, which would either cancel the entry or fire the stop.
  The comparison basis is put right BEFORE the gap test, never after.

  BOTH TOUCHED IN ONE BAR RESOLVES TO THE STOP. A daily bar records that the
  low reached the stop and the high reached the target, and nothing about which
  came first. Choosing the target would be choosing the profitable
  interpretation of an unknowable sequence.
"""

from __future__ import annotations

from dataclasses import dataclass

PROTOCOL_VERSION = "R1-PROTOCOL-1.1"

# Dollar ADV bands for slippage, from the frozen config. There is deliberately
# no band below $5M: see slippage_for_adv.
ADV_HIGH_LIQUIDITY = 50_000_000.0
ADV_MID_LIQUIDITY = 5_000_000.0

BPS = 1e-4


class ProtocolError(ValueError):
    """Raised when the protocol has no defined answer for an input."""


def slippage_for_adv(adv_dollar: float | None, high_bps: float, mid_bps: float) -> float:
    """Slippage in bps for a dollar ADV, or a refusal.

    The protocol defines two bands: above $50M and $5M-$50M. It defines nothing
    below $5M. Defaulting such a security to the mid band would be inventing a
    parameter in the direction that makes the result look better, so this
    raises and the caller cancels the entry instead. An ADV we could not compute
    is refused for the same reason.
    """
    if adv_dollar is None:
        raise ProtocolError("dollar ADV could not be computed, so no slippage band applies")
    if adv_dollar > ADV_HIGH_LIQUIDITY:
        return float(high_bps)
    if adv_dollar >= ADV_MID_LIQUIDITY:
        return float(mid_bps)
    raise ProtocolError(
        f"dollar ADV {adv_dollar:,.0f} is below the ${ADV_MID_LIQUIDITY:,.0f} floor of "
        f"the protocol's slippage bands. R1-PROTOCOL-1.1 defines no band there and a "
        f"parameter is not invented to fill the gap"
    )


def entry_fill(open_price: float, slippage_bps: float) -> float:
    """Entry pays up: open * (1 + s)."""
    return open_price * (1.0 + slippage_bps * BPS)


def exit_fill(price: float, slippage_bps: float) -> float:
    """Every exit gets less: price * (1 - s). Stop, target, gap and time alike."""
    return price * (1.0 - slippage_bps * BPS)


def stop_and_target(fill: float, atr: float, stop_multiple: float,
                    target_multiple: float) -> tuple[float, float]:
    """Both from the ACTUAL FILL, never from the signal close.

    Setting them from the signal close would silently move the risk on every
    trade that gapped, and in the favourable direction for anything that gapped
    down. The ATR passed here is frozen at entry and never recomputed.
    """
    return fill - stop_multiple * atr, fill + target_multiple * atr


@dataclass(frozen=True)
class GapDecision:
    cancelled: bool
    open_price: float
    raw_prior_close: float
    adjusted_prior_close: float
    split_ratio: float
    atr_at_entry_basis: float
    gap: float
    gap_atr: float
    basis_note: str


def gap_test(*, open_price: float, raw_prior_close: float, split_ratio: float,
             atr_at_entry_basis: float, limit_atr: float) -> GapDecision:
    """Cancel when the open exceeds the adjusted prior close by more than 1 ATR.

    ONE-SIDED, as written: "if the open exceeds the adjusted prior close". A gap
    DOWN is a better entry for a long and is not a reason to cancel; only
    chasing a gap up is.

    CORPORATE ACTIONS FIRST. split_ratio is the ratio of any split whose ex-date
    is the entry session, and the prior close is divided by it before the
    comparison. For a 10-for-1 split a $1,200 prior close becomes $120, which is
    where the stock actually opens, and the gap is ~0 rather than -90%.

    The ATR arrives already on the post-split basis: F3 adjusts prices at read
    time to the newest split basis, so an ATR computed from that series is
    already expressed in the units the entry-day open is quoted in. The
    adjustment rule 2 demands is therefore the identity for the ATR and real for
    the prior close, and both are recorded so a reader can see it.
    """
    adjusted_prior_close = raw_prior_close / split_ratio
    gap = open_price - adjusted_prior_close
    gap_atr = gap / atr_at_entry_basis if atr_at_entry_basis > 0 else float("inf")
    note = (
        f"raw prior close {raw_prior_close:.4f}"
        + (
            f" divided by the {split_ratio} split ratio on the entry session "
            f"= {adjusted_prior_close:.4f}"
            if split_ratio != 1.0 else
            " (no split on the entry session, basis unchanged)"
        )
        + f"; open {open_price:.4f}; gap {gap:+.4f} = {gap_atr:+.3f} ATR "
        f"(ATR {atr_at_entry_basis:.4f}, limit {limit_atr} ATR)"
    )
    return GapDecision(
        cancelled=gap > limit_atr * atr_at_entry_basis,
        open_price=open_price,
        raw_prior_close=raw_prior_close,
        adjusted_prior_close=adjusted_prior_close,
        split_ratio=split_ratio,
        atr_at_entry_basis=atr_at_entry_basis,
        gap=gap,
        gap_atr=gap_atr,
        basis_note=note,
    )


@dataclass(frozen=True)
class ExitDecision:
    exit: bool
    reason: str | None
    raw_price: float | None
    note: str


def evaluate_bar(*, open_price: float, high: float, low: float, close: float,
                 stop: float, target: float, held_sessions: int,
                 horizon_days: int) -> ExitDecision:
    """One daily bar against a position whose corporate actions are already applied.

    Order matters and is fixed:

      1. Open already beyond the stop -> exit AT THE OPEN. The stop was never
         available at its own price; claiming it would manufacture the
         difference as free money on every gap down.
      2. Open already beyond the target -> exit AT THE OPEN, for the mirror
         reason. This one costs the position money, which is the point: the
         rule is symmetric and applied whichever way it falls.
      3. Low reached the stop -> exit at the stop.
      4. High reached the target -> exit at the target.
      5. Both -> the stop, per the module docstring.
      6. Maximum hold reached -> exit at the close.

    Prices returned here are RAW. Adverse slippage is applied by the caller.
    """
    if open_price <= stop:
        return ExitDecision(True, "gap_through_stop", open_price,
                            f"open {open_price:.4f} at or below the stop {stop:.4f}")
    if open_price >= target:
        return ExitDecision(True, "gap_through_target", open_price,
                            f"open {open_price:.4f} at or above the target {target:.4f}")

    hit_stop = low <= stop
    hit_target = high >= target
    if hit_stop and hit_target:
        return ExitDecision(
            True, "stop", stop,
            f"low {low:.4f} reached the stop {stop:.4f} and high {high:.4f} reached "
            f"the target {target:.4f} in one bar; the daily bar records no intraday "
            f"sequence, so the stop is assumed first"
        )
    if hit_stop:
        return ExitDecision(True, "stop", stop, f"low {low:.4f} reached the stop {stop:.4f}")
    if hit_target:
        return ExitDecision(True, "target", target,
                            f"high {high:.4f} reached the target {target:.4f}")
    if held_sessions >= horizon_days:
        return ExitDecision(True, "time_exit", close,
                            f"maximum hold of {horizon_days} trading sessions reached")
    return ExitDecision(False, None, None,
                        f"session {held_sessions} of {horizon_days}, no level reached")


def pnl(*, shares: float, entry_price: float, exit_price: float,
        dividends: float, notional: float) -> tuple[float, float, float]:
    """(gross, net, pct). Dividends are part of the net result, not the gross.

    pnl_pct is against the notional actually committed, so a position that was
    never resized cannot report a percentage that a different position size
    would have produced.
    """
    gross = shares * (exit_price - entry_price)
    net = gross + dividends
    return gross, net, net / notional
