"""Eligibility, ordering, caps and cooldowns. Pure functions, no I/O.

Everything here is deterministic by construction. Selection is fully automatic
and no human may add, remove or reorder candidates, which means the ordering can
never depend on anything the database is free to vary -- not insertion order, not
the query plan, not a dict iteration order. The sort key is total: composite,
then Quality, then inputs_complete, then security_id. security_id is unique, so
two securities can never compare equal and the result cannot wobble between runs.

The other rule shaping this file: a security that is considered and not selected
is LOGGED, never dropped. A candidate list on its own cannot be audited, because
there is no way to tell a security that failed a rule from one the code never
looked at.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DILUTION_DISQUALIFY_FLAGS = (
    "rapid_share_growth", "active_issuance", "atm_or_convertible",
)


@dataclass(frozen=True)
class Row:
    """One security as it stands at the cutoff."""

    security_id: int
    symbol: str
    cohort_id: str
    rankable: bool
    model_applicable: bool
    composite: float | None
    rank: int | None
    quality: float | None
    inputs_complete: int
    dilution_score: float
    dilution_disqualified: bool
    high_going_concern: bool
    high_dilution_flags: tuple[str, ...] = ()
    last_exit_session: str | None = None
    last_gap_cancel_session: str | None = None
    open_horizons: tuple[int, ...] = ()


@dataclass
class Suppression:
    security_id: int
    horizon_days: int
    composite: float | None
    rank: int | None
    reason: str
    detail: str


@dataclass
class SelectionResult:
    selected: list[Row] = field(default_factory=list)
    suppressions: list[Suppression] = field(default_factory=list)

    def suppress(self, row: Row, horizons, reason: str, detail: str) -> None:
        for horizon in horizons:
            self.suppressions.append(
                Suppression(row.security_id, horizon, row.composite, row.rank, reason, detail)
            )


def sort_key(row: Row) -> tuple:
    """Total order: composite desc, Quality desc, inputs_complete desc, id asc.

    Negation rather than reverse=True, so every tiebreak direction is explicit
    in one expression and a later reader cannot mistake which way a field sorts.
    """
    return (
        -(row.composite if row.composite is not None else -1.0),
        -(row.quality if row.quality is not None else -1.0),
        -row.inputs_complete,
        row.security_id,
    )


def eligibility_failure(
    row: Row, threshold: float | None, dilution_limit: float
) -> tuple[str, str] | None:
    """The FIRST rule this security fails, or None. Order is deliberate.

    Cheap structural facts are tested before the threshold, so the reason a
    security was rejected is the most fundamental one rather than whichever
    happened to be checked first.
    """
    if not row.rankable:
        return "not_rankable", "the security carries no composite score at the cutoff"
    if not row.model_applicable:
        return (
            "model_not_applicable",
            "model not supported: SIC division H carries model_applicable = 0 from F5",
        )
    if row.dilution_disqualified or row.dilution_score >= dilution_limit:
        return (
            "dilution_disqualified",
            f"dilution score {row.dilution_score:.1f} at or above the "
            f"disqualification threshold of {dilution_limit:.0f}",
        )
    if row.high_going_concern:
        return (
            "risk_flag_going_concern",
            "carries a severity-high going_concern risk flag",
        )
    if row.high_dilution_flags:
        return (
            "risk_flag_dilution_disqualify",
            "carries severity-high dilution risk flag(s): "
            + ", ".join(sorted(row.high_dilution_flags)),
        )
    if threshold is None:
        return (
            "composite_threshold_unset",
            "composite_threshold is still the declared null placeholder, so the "
            "eligibility test 'composite >= threshold' cannot be evaluated. It is "
            "set in Phase S. No candidate is selected on a threshold that does "
            "not exist",
        )
    if row.composite is None or row.composite < threshold:
        return (
            "below_composite_threshold",
            f"composite {row.composite:.4f} is below the configured threshold "
            f"of {threshold}",
        )
    return None


def cooldown_failure(
    row: Row, exit_cutoff_session: str | None, gap_cutoff_session: str | None,
    exit_days: int, gap_days: int,
) -> tuple[str, str] | None:
    """Recent exits and gap cancellations, both counted in TRADING days."""
    if (
        row.last_exit_session is not None
        and exit_cutoff_session is not None
        and row.last_exit_session > exit_cutoff_session
    ):
        return (
            "cooldown_recent_exit",
            f"a position exited on {row.last_exit_session}, inside the {exit_days} "
            f"trading-day cooldown that begins after {exit_cutoff_session}",
        )
    if (
        row.last_gap_cancel_session is not None
        and gap_cutoff_session is not None
        and row.last_gap_cancel_session > gap_cutoff_session
    ):
        return (
            "cooldown_gap_cancelled",
            f"a candidate was gap-cancelled on {row.last_gap_cancel_session}, inside "
            f"the {gap_days} trading-day cooldown that begins after {gap_cutoff_session}",
        )
    return None


def select(
    rows: list[Row],
    *,
    horizons: list[int],
    threshold: float | None,
    dilution_limit: float,
    max_candidates: int,
    max_per_cohort: int,
    exit_cutoff_session: str | None,
    gap_cutoff_session: str | None,
    exit_cooldown_days: int,
    gap_cooldown_days: int,
    book_capacity: dict[int, int],
) -> SelectionResult:
    """The whole rule, in one deterministic pass.

    Selection is horizon-agnostic: one selection produces up to `max_candidates`
    candidates and each opens a position in EVERY book. Per-book conditions --
    an already-open position, an exhausted book -- therefore suppress a candidate
    for that book alone, and are recorded per horizon.
    """
    result = SelectionResult()

    survivors: list[Row] = []
    for row in sorted(rows, key=sort_key):
        failure = eligibility_failure(row, threshold, dilution_limit)
        if failure is None:
            failure = cooldown_failure(
                row, exit_cutoff_session, gap_cutoff_session,
                exit_cooldown_days, gap_cooldown_days,
            )
        if failure is not None:
            result.suppress(row, horizons, failure[0], failure[1])
            continue
        survivors.append(row)

    per_cohort: dict[str, int] = {}
    for row in survivors:
        # Per-book conditions first: they explain the security's own state, and
        # a candidate blocked in every book must not consume a scarce slot.
        blocked: list[int] = []
        for horizon in horizons:
            if horizon in row.open_horizons:
                result.suppressions.append(Suppression(
                    row.security_id, horizon, row.composite, row.rank, "open_position",
                    f"a position is already open for this security at the {horizon}-day "
                    f"horizon; the qualifying signal is logged rather than discarded",
                ))
                blocked.append(horizon)
            elif book_capacity.get(horizon, 0) <= 0:
                result.suppressions.append(Suppression(
                    row.security_id, horizon, row.composite, row.rank, "book_capacity",
                    f"the {horizon}-day book has no remaining capacity",
                ))
                blocked.append(horizon)
        if len(blocked) == len(horizons):
            continue

        if len(result.selected) >= max_candidates:
            result.suppress(
                row, [h for h in horizons if h not in blocked], "selection_cap",
                f"the weekly maximum of {max_candidates} candidates was already filled "
                f"by higher-ranked securities",
            )
            continue
        if per_cohort.get(row.cohort_id, 0) >= max_per_cohort:
            result.suppress(
                row, [h for h in horizons if h not in blocked], "cohort_cap",
                f"cohort {row.cohort_id} already has {max_per_cohort} candidates, "
                f"the configured maximum from one SIC-derived cohort",
            )
            continue

        result.selected.append(row)
        per_cohort[row.cohort_id] = per_cohort.get(row.cohort_id, 0) + 1
        for horizon in blocked:
            book_capacity[horizon] = book_capacity.get(horizon, 0)
        for horizon in horizons:
            if horizon not in blocked:
                book_capacity[horizon] = book_capacity.get(horizon, 0) - 1

    return result
