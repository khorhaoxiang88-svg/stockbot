"""The trading week, derived from the price calendar rather than hard-coded.

Named trading_calendar, not calendar: running a module inside this package as a
script puts the package directory on sys.path, and a file named calendar.py
there shadows the standard library's calendar module. datetime.strptime imports
it, so the collision surfaces far from its cause as
"module 'calendar' has no attribute 'day_abbr'".

SELECTION-RULE-1.1 says official selection runs once per US trading week, after
that week's final regular session closes. Two things follow, and both are easy
to get subtly wrong.

WHICH SESSION IS THE WEEK'S LAST. Not Friday. Good Friday, Christmas Day and a
presidential funeral all move it, and NYSE half-days do not. Rather than ship a
holiday table that will be wrong the first time the exchange closes
unexpectedly, the week's final session is read from the sessions we actually
have bars for.

WHETHER THAT WEEK IS OVER. This is the part a naive "max date in the week"
misses. If the dataset ends on a Wednesday, that Wednesday is the maximum date
in its week, but the week is not finished -- it is the data that ran out. A week
counts as over only when a session in a LATER week exists (the exchange
demonstrably moved on), or when the calendar has passed the Saturday of that
week (no US regular session falls at a weekend, so nothing more can arrive).

A selection run for an incomplete week is refused, not silently run early.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Regular-session close. Half-days close at 13:00 ET; using 16:00 for those
# widens the evidence window by three hours, which can only ever include
# evidence that was already public, never exclude evidence that was.
REGULAR_CLOSE = time(16, 0)


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in str(value)[:10].split("-"))
    return date(year, month, day)


def week_key(day: date) -> tuple[int, int]:
    """ISO year and week. ISO is used so the week rolls on Monday, not Sunday."""
    iso = day.isocalendar()
    return iso[0], iso[1]


@dataclass(frozen=True)
class TradingWeek:
    year: int
    week: int
    sessions: list[str]
    complete: bool

    @property
    def first_session(self) -> str:
        return self.sessions[0]

    @property
    def final_session(self) -> str:
        return self.sessions[-1]


def trading_weeks(session_dates: list[str], as_of: str | None = None) -> list[TradingWeek]:
    """Group sessions into ISO weeks, marking each complete or not.

    A week is complete when EITHER is true:

      * a session exists in a LATER week. This is the strong form: our data
        spans the week boundary, so the sessions we hold for this week are all
        of them.
      * the calendar has passed the week's Saturday AND the newest session we
        hold for the week is that week's Friday. This covers the ordinary
        weekend case, where selection should run on Friday evening rather than
        waiting for Monday's bars to arrive.

    The Friday condition in the second clause is what stops a truncated week
    from masquerading as a finished one. A dataset that stops on Wednesday
    still has a "maximum date in the week", and without the check that
    Wednesday would be treated as the week's close -- the data ran out, the week
    did not end. When the exchange is shut on a Friday the second clause simply
    does not fire and selection waits for Monday's session, which is a one
    session delay rather than a wrong answer.
    """
    ordered = sorted(set(session_dates))
    grouped: dict[tuple[int, int], list[str]] = {}
    for value in ordered:
        grouped.setdefault(week_key(parse_date(value)), []).append(value)

    keys = sorted(grouped)
    target = parse_date(as_of) if as_of else None
    weeks = []
    for index, key in enumerate(keys):
        later_session_exists = index < len(keys) - 1
        weekend_settled = False
        if target is not None:
            monday = date.fromisocalendar(key[0], key[1], 1)
            final = parse_date(grouped[key][-1])
            weekend_settled = (
                target >= monday + timedelta(days=5) and final.isoweekday() == 5
            )
        weeks.append(TradingWeek(
            key[0], key[1], grouped[key],
            complete=later_session_exists or weekend_settled,
        ))
    return weeks


def latest_complete_week(session_dates: list[str], as_of: str) -> TradingWeek | None:
    """The newest week that both ended at or before as_of and is provably over."""
    target = parse_date(as_of)
    for week in reversed(trading_weeks(session_dates, as_of)):
        if not week.complete:
            continue
        if parse_date(week.final_session) <= target:
            return week
    return None


def week_containing(session_dates: list[str], day: str) -> TradingWeek | None:
    key = week_key(parse_date(day))
    for week in trading_weeks(session_dates):
        if (week.year, week.week) == key:
            return week
    return None


def session_close_utc(session: str) -> str:
    """The regular close of an ET trading date, as a UTC timestamp.

    Storage is UTC everywhere; the ET conversion happens here because the
    exchange closes at a wall-clock time, and the UTC instant of 16:00 ET shifts
    by an hour across the daylight-saving boundary.
    """
    eastern = datetime.combine(parse_date(session), REGULAR_CLOSE, tzinfo=EASTERN)
    return eastern.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def trading_days_between(session_dates: list[str], start: str, end: str) -> int:
    """Sessions strictly after `start`, up to and including `end`.

    Cooldowns are stated in TRADING days, so a long weekend must not burn three
    of them.
    """
    return sum(1 for value in sorted(set(session_dates)) if start < value <= end)


def sessions_back(session_dates: list[str], end: str, count: int) -> str | None:
    """The session `count` trading days before `end`, or None if not that deep."""
    ordered = [value for value in sorted(set(session_dates)) if value <= end]
    if len(ordered) <= count:
        return None
    return ordered[-1 - count]
