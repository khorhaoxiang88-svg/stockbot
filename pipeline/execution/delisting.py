"""Delisting resolution. Deterministic, no discretion.

The rule this module exists to enforce: NEVER close a position automatically at
the last quoted price. A delisted security's final print may have been in a
market with no depth, no buyer, or no trade at all -- OTC pink sheets routinely
print a "last sale" that nothing could actually execute at. Treating that print
as an exit price would silently launder an unexecutable number into reported
P&L.

So a delisting does not exit a position. It moves the status to
pending_resolution and leaves it there, evaluated on every run
(`last_evaluated_at` keeps updating), until one of two things happens:

  1. A recovery is VERIFIABLE -- contractually established cash or merger
     consideration, or an executable OTC bid / conservative supported
     quotation. This system integrates neither data source yet: there is no
     feed for merger-consideration terms or OTC NBBO in the fixture. So this
     path is implemented and tested against constructed input, but the live
     run never takes it, and that absence is reported rather than silently
     producing a plausible-looking number from nothing.

  2. 180 days pass with no verifiable recovery. Only then does the recorded
     resolution policy value the common equity at ZERO. Zero is chosen because
     it is the one number that requires no judgement call -- any other haircut
     would be a discretionary choice dressed up as a policy, which rule 4
     forbids outright ("NO manually chosen haircut may ever be used to improve
     a result").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

RESOLUTION_POLICY_VERSION = 1
UNRESOLVED_GRACE_DAYS = 180


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in str(value)[:10].split("-"))
    return date(year, month, day)


@dataclass(frozen=True)
class VerifiedRecovery:
    """A contractually established or executable recovery value.

    Constructed and passed in by the caller; this module never fetches
    anything itself. Nothing in this system currently produces one, which is
    exactly why the field is Optional everywhere it is threaded through: an
    absent recovery is not an error, it is the ordinary case until a merger-
    consideration or OTC-quote source is integrated.
    """

    price_per_share: float
    basis: str          # 'merger_consideration' or 'otc_executable_bid'
    source_reference: str
    verified_at: str    # UTC


@dataclass(frozen=True)
class DelistingDecision:
    action: str          # 'stay_pending' | 'resolve_recovery' | 'resolve_zero'
    exit_price: float | None
    exit_reason: str | None
    note: str


def resolve(
    *,
    delisted_date: str,
    as_of_date: str,
    verified_recovery: VerifiedRecovery | None,
) -> DelistingDecision:
    """What to do with a pending_resolution position today.

    Order is fixed and matches the protocol: a verified recovery wins whenever
    it exists, at any time, because it is a fact rather than a deadline. Only in
    its absence does the 180-day clock matter at all.
    """
    if verified_recovery is not None:
        return DelistingDecision(
            "resolve_recovery",
            verified_recovery.price_per_share,
            (
                "delisting_resolved_consideration"
                if verified_recovery.basis == "merger_consideration"
                else "delisting_resolved_market"
            ),
            f"verified recovery {verified_recovery.price_per_share} per share via "
            f"{verified_recovery.basis} ({verified_recovery.source_reference}), "
            f"verified {verified_recovery.verified_at}",
        )

    elapsed = (parse_date(as_of_date) - parse_date(delisted_date)).days
    if elapsed < UNRESOLVED_GRACE_DAYS:
        return DelistingDecision(
            "stay_pending", None, None,
            f"{elapsed} of {UNRESOLVED_GRACE_DAYS} days since delisting "
            f"({delisted_date}); no verifiable recovery on file. Remains "
            f"pending_resolution and stays in reported exposure",
        )

    return DelistingDecision(
        "resolve_zero", 0.0, "delisting_zero_after_180d",
        f"{elapsed} days since delisting ({delisted_date}) with no verifiable "
        f"cash or merger consideration and no executable market quote on file. "
        f"Common equity valued at ZERO under resolution policy "
        f"v{RESOLUTION_POLICY_VERSION}. No manually chosen haircut was applied.",
    )


def next_evaluation_note(delisted_date: str, as_of_date: str) -> str:
    """Human-readable remaining time, for last_evaluated_at bookkeeping."""
    elapsed = (parse_date(as_of_date) - parse_date(delisted_date)).days
    remaining = max(0, UNRESOLVED_GRACE_DAYS - elapsed)
    return f"{remaining} days remain before zero-resolution absent a verified recovery"
