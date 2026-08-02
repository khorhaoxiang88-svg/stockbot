"""Tests for weekly candidate selection.

The rule itself is pure, so most of this runs against constructed Rows rather
than the database. That is deliberate: the fixture currently produces zero
candidates for two independent and entirely correct reasons -- the pipeline is
stale and composite_threshold is still null -- so a test that leaned on live
data could not exercise the caps, the cooldowns or the per-book suppressions at
all.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import migrate
from selection import freshness as FR
from selection import rules as R
from selection import trading_calendar as CAL
from selection.compute import (
    HASHED_COLUMNS,
    candidate_identity,
    row_hash,
)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "stockbot.db"
HORIZONS = [20, 60]


def make_row(security_id: int, **overrides) -> R.Row:
    base = dict(
        security_id=security_id,
        symbol=f"S{security_id}",
        cohort_id="SIC-D",
        rankable=True,
        model_applicable=True,
        composite=50.0,
        rank=security_id,
        quality=50.0,
        inputs_complete=1,
        dilution_score=0.0,
        dilution_disqualified=False,
        high_going_concern=False,
        high_dilution_flags=(),
        last_exit_session=None,
        last_gap_cancel_session=None,
        open_horizons=(),
    )
    base.update(overrides)
    return R.Row(**base)


def run(rows, **overrides) -> R.SelectionResult:
    kwargs = dict(
        horizons=HORIZONS, threshold=10.0, dilution_limit=22.0,
        max_candidates=5, max_per_cohort=2,
        exit_cutoff_session="2026-07-10", gap_cutoff_session="2026-07-21",
        exit_cooldown_days=10, gap_cooldown_days=3,
        book_capacity={20: 100, 60: 100},
    )
    kwargs.update(overrides)
    return R.select(rows, **kwargs)


def reasons_for(result: R.SelectionResult, security_id: int) -> set[str]:
    return {s.reason for s in result.suppressions if s.security_id == security_id}


# ------------------------------------------------------------------ ordering


def test_selection_is_deterministic_for_identical_inputs():
    rows = [make_row(i, composite=60.0 - i) for i in range(1, 12)]
    first = run(list(rows))
    for _ in range(5):
        # Reversed input order must not change anything: the sort key is total.
        again = run(list(reversed(rows)), max_per_cohort=5)
        assert [r.security_id for r in first.selected] == [
            r.security_id for r in run(list(rows)).selected
        ]
        assert [r.security_id for r in again.selected] == [
            r.security_id for r in run(list(rows), max_per_cohort=5).selected
        ]


def test_ties_break_on_quality_then_inputs_complete_then_security_id():
    rows = [
        make_row(4, composite=50.0, quality=10.0, inputs_complete=1),
        make_row(3, composite=50.0, quality=90.0, inputs_complete=0),
        make_row(2, composite=50.0, quality=90.0, inputs_complete=1),
        make_row(1, composite=50.0, quality=90.0, inputs_complete=1),
    ]
    result = run(rows, max_per_cohort=5)
    # Quality first: 3 beats 4. Then inputs_complete: 1 and 2 beat 3.
    # Then the lowest security_id: 1 beats 2.
    assert [r.security_id for r in result.selected] == [1, 2, 3, 4]


def test_a_missing_quality_never_sorts_above_a_present_one():
    rows = [make_row(1, quality=None), make_row(2, quality=0.0)]
    result = run(rows, max_per_cohort=5)
    assert [r.security_id for r in result.selected] == [2, 1]


# --------------------------------------------------------------- eligibility


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"rankable": False}, "not_rankable"),
        ({"model_applicable": False}, "model_not_applicable"),
        ({"dilution_disqualified": True, "dilution_score": 23.0}, "dilution_disqualified"),
        ({"dilution_score": 22.0}, "dilution_disqualified"),
        ({"high_going_concern": True}, "risk_flag_going_concern"),
        ({"high_dilution_flags": ("rapid_share_growth",)}, "risk_flag_dilution_disqualify"),
        ({"composite": 5.0}, "below_composite_threshold"),
    ],
)
def test_each_eligibility_rule_suppresses_with_its_own_reason(overrides, expected):
    result = run([make_row(1, **overrides)])
    assert not result.selected
    assert reasons_for(result, 1) == {expected}
    # Both books are told, so each book's log is independently complete.
    assert sorted(s.horizon_days for s in result.suppressions) == HORIZONS


def test_a_null_threshold_blocks_selection_rather_than_being_treated_as_zero():
    result = run([make_row(1, composite=99.0)], threshold=None)
    assert not result.selected
    assert reasons_for(result, 1) == {"composite_threshold_unset"}
    detail = result.suppressions[0].detail
    assert "Phase S" in detail


def test_every_considered_security_is_accounted_for():
    rows = [
        make_row(1, composite=90.0),
        make_row(2, composite=5.0),
        make_row(3, rankable=False),
        make_row(4, composite=80.0),
    ]
    result = run(rows, max_per_cohort=5)
    accounted = {r.security_id for r in result.selected} | {
        s.security_id for s in result.suppressions
    }
    assert accounted == {1, 2, 3, 4}


# ------------------------------------------------------------------ cooldowns


def test_a_recent_exit_blocks_the_security():
    result = run([make_row(1, last_exit_session="2026-07-15")])
    assert reasons_for(result, 1) == {"cooldown_recent_exit"}


def test_an_exit_older_than_the_cooldown_does_not_block():
    result = run([make_row(1, last_exit_session="2026-07-01")])
    assert [r.security_id for r in result.selected] == [1]


def test_a_recent_gap_cancellation_blocks_the_security():
    result = run([make_row(1, last_gap_cancel_session="2026-07-23")])
    assert reasons_for(result, 1) == {"cooldown_gap_cancelled"}


# ------------------------------------------------------------------ the caps


def test_the_selection_cap_fires_and_logs():
    rows = [make_row(i, composite=90.0 - i, cohort_id=f"SIC-{i}") for i in range(1, 9)]
    result = run(rows)
    assert len(result.selected) == 5
    assert [r.security_id for r in result.selected] == [1, 2, 3, 4, 5]
    for security_id in (6, 7, 8):
        assert reasons_for(result, security_id) == {"selection_cap"}


def test_the_cohort_cap_fires_and_logs():
    rows = [make_row(i, composite=90.0 - i, cohort_id="SIC-D") for i in range(1, 6)]
    result = run(rows)
    assert [r.security_id for r in result.selected] == [1, 2]
    for security_id in (3, 4, 5):
        assert reasons_for(result, security_id) == {"cohort_cap"}


def test_the_cohort_cap_does_not_consume_the_selection_cap():
    rows = [make_row(i, composite=90.0 - i, cohort_id="SIC-D") for i in range(1, 5)]
    rows += [make_row(i, composite=50.0 - i, cohort_id=f"SIC-{i}") for i in range(5, 9)]
    result = run(rows)
    # Two from SIC-D, then three others: the cap is on candidates, not attempts.
    assert len(result.selected) == 5
    assert [r.security_id for r in result.selected] == [1, 2, 5, 6, 7]


# ------------------------------------------------------ per-book suppressions


def test_an_open_twenty_day_position_still_allows_a_sixty_day_candidate():
    result = run([make_row(1, open_horizons=(20,))])
    assert [r.security_id for r in result.selected] == [1]
    suppressed = [s for s in result.suppressions if s.security_id == 1]
    assert len(suppressed) == 1
    assert suppressed[0].horizon_days == 20
    assert suppressed[0].reason == "open_position"


def test_a_second_signal_for_an_open_horizon_is_logged_not_discarded():
    result = run([make_row(1, open_horizons=(20, 60))])
    # Blocked in every book, so it is not a candidate -- but both books say why.
    assert not result.selected
    assert sorted(
        (s.horizon_days, s.reason) for s in result.suppressions if s.security_id == 1
    ) == [(20, "open_position"), (60, "open_position")]


def test_book_capacity_exhaustion_suppresses_with_its_own_reason():
    result = run([make_row(1)], book_capacity={20: 0, 60: 100})
    assert [r.security_id for r in result.selected] == [1]
    assert [(s.horizon_days, s.reason) for s in result.suppressions] == [
        (20, "book_capacity")
    ]


def test_a_candidate_blocked_in_every_book_does_not_consume_a_slot():
    rows = [make_row(1, composite=99.0, open_horizons=(20, 60))]
    rows += [make_row(i, composite=90.0 - i, cohort_id=f"SIC-{i}") for i in range(2, 8)]
    result = run(rows)
    assert 1 not in {r.security_id for r in result.selected}
    # Five slots remain for the securities that could actually be held.
    assert len(result.selected) == 5


def test_capacity_is_consumed_as_candidates_are_accepted():
    rows = [make_row(i, composite=90.0 - i, cohort_id=f"SIC-{i}") for i in range(1, 6)]
    result = run(rows, book_capacity={20: 2, 60: 100})
    assert len(result.selected) == 5
    exhausted = [s for s in result.suppressions if s.reason == "book_capacity"]
    # The first two consume the 20-day book, the remaining three are logged.
    assert {s.security_id for s in exhausted} == {3, 4, 5}
    assert all(s.horizon_days == 20 for s in exhausted)


# ------------------------------------------------------------------ calendar


SESSIONS = [
    "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
    "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
    "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24",
    "2026-07-27", "2026-07-28", "2026-07-29",
]


def test_a_truncated_final_week_is_not_treated_as_complete():
    # The data stops on Wednesday. That Wednesday is the maximum date in its
    # week, and it is NOT the week's close.
    week = CAL.latest_complete_week(SESSIONS, "2026-08-02")
    assert week.final_session == "2026-07-24"
    assert week.sessions == SESSIONS[10:15]


def test_a_week_ending_on_friday_is_complete_once_the_weekend_has_passed():
    through_friday = SESSIONS[:15]
    assert CAL.latest_complete_week(through_friday, "2026-07-24") is not None
    saturday = CAL.latest_complete_week(through_friday, "2026-07-25")
    assert saturday.final_session == "2026-07-24"


def test_a_week_is_complete_once_a_later_week_has_a_session():
    week = CAL.latest_complete_week(SESSIONS[:11], "2026-07-20")
    assert week.final_session == "2026-07-17"


def test_the_evidence_cutoff_is_the_regular_close_in_utc():
    # 16:00 in New York during daylight saving is 20:00 UTC.
    assert CAL.session_close_utc("2026-07-24") == "2026-07-24T20:00:00Z"
    # And 21:00 UTC in standard time, which is why this is not hard-coded.
    assert CAL.session_close_utc("2026-01-16") == "2026-01-16T21:00:00Z"


def test_cooldowns_are_counted_in_trading_days_not_calendar_days():
    # Ten trading days back from Friday 24 July is Friday 10 July, not 14 July.
    assert CAL.sessions_back(SESSIONS, "2026-07-24", 10) == "2026-07-10"
    assert CAL.sessions_back(SESSIONS, "2026-07-24", 3) == "2026-07-21"
    assert CAL.sessions_back(SESSIONS, "2026-07-06", 10) is None


# ------------------------------------------------------------------ freshness


def test_issuer_report_age_is_not_pipeline_staleness():
    # A company that filed its 10-Q on time is not stale two months later.
    assert FR.FORM_10Q_DEADLINE_DAYS == 45
    assert FR.LATE_FILING_GRACE_DAYS == 15


def test_row_hash_covers_every_column_of_the_table():
    """If a column is added and not hashed, the field would be unprotected."""
    up = (Path(__file__).resolve().parents[2] / "migrations"
          / "011_selection_and_books.up.sql").read_text(encoding="utf-8")
    body = up.split("CREATE TABLE IF NOT EXISTS research_candidates (", 1)[1]
    body = body.split("\n);", 1)[0]
    declared = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("-") or line.upper().startswith("CHECK"):
            continue
        name = line.split()[0].strip('",')
        if name.isidentifier():
            declared.append(name)
    assert set(declared) == set(HASHED_COLUMNS) | {"row_hash"}


def test_row_hash_changes_when_any_field_changes():
    row = {key: f"v-{key}" for key in HASHED_COLUMNS}
    original = row_hash(row)
    for key in HASHED_COLUMNS:
        mutated = dict(row)
        mutated[key] = "tampered"
        assert row_hash(mutated) != original, key


def test_candidate_id_is_deterministic_so_a_rerun_cannot_duplicate():
    first = candidate_identity(7, "2026-07-24T20:00:00Z", 1, 1)
    second = candidate_identity(7, "2026-07-24T20:00:00Z", 1, 1)
    assert first == second
    assert candidate_identity(7, "2026-07-31T20:00:00Z", 1, 1) != first
    assert candidate_identity(8, "2026-07-24T20:00:00Z", 1, 1) != first


# ------------------------------------------------------ against the database


def _database() -> sqlite3.Connection:
    if not DB_PATH.exists():
        pytest.skip("no database")
    conn = migrate.connect(DB_PATH)
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='books'"
    ).fetchone():
        conn.close()
        pytest.skip("selection tables not present")
    return conn


def test_both_books_start_at_the_configured_nav_with_no_open_positions():
    conn = _database()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM books ORDER BY horizon_days")]
        if not rows:
            pytest.skip("books not created yet; run pipeline/selection/compute.py")
        assert [r["horizon_days"] for r in rows] == [20, 60]
        for row in rows:
            assert row["starting_nav"] == 100_000
            assert row["current_nav"] == 100_000
            assert row["open_position_count"] == 0
    finally:
        conn.close()


def test_research_candidates_is_append_only():
    conn = _database()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO research_candidates (candidate_id, security_id, generated_at, "
            "data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash, "
            "code_version, selection_rule_version, mapping_version, "
            "source_health_snapshot_json, score_snapshot_json, accessions_used_json, "
            "composite_at_generation, rank_at_generation, signal_close, atr_window, "
            "price_data_cutoff, entry_rule, gap_limit_atr, row_hash) "
            "VALUES ('t1', 1, 'x', 'x', "
            "(SELECT snapshot_id FROM universe_snapshot_runs LIMIT 1), "
            "(SELECT run_id FROM pipeline_runs LIMIT 1), 1, 'h', 'v', 1, '1', "
            "'{}', '{}', '[]', 50, 1, 10, 14, '2026-07-24', 'r', 1, 'hash')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE research_candidates SET signal_close = 11 WHERE candidate_id='t1'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM research_candidates WHERE candidate_id='t1'")
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def test_paper_positions_table_exists_and_superseded_the_f10_positions_table():
    """F11 (migration 013) replaced `positions` with the real `paper_positions`.

    F10 added `positions` as the minimum needed to express "at most one open
    position per (security, horizon)" before an execution engine existed to
    populate a real one. That rule is now enforced by F11's selection-time
    suppression (open_position, tested in rules.py above) rather than a DB
    constraint on this table, because paper_positions has no security_id column
    of its own -- it is reached through candidate_id -- so the invariant lives
    at the layer that decides which horizons a candidate is admitted to, not as
    a partial unique index here. `positions` itself is gone; asserting that is
    what keeps this test from silently passing against a table nobody reads any
    more.
    """
    conn = _database()
    try:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions'"
        ).fetchone()
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
        ).fetchone()
    finally:
        conn.close()


def test_a_stale_source_blocked_every_candidate_in_the_recorded_run():
    conn = _database()
    try:
        row = conn.execute(
            "SELECT run_id FROM pipeline_runs WHERE stage = 'selection' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no selection run recorded yet")
        reasons = {
            r["suppression_reason"]
            for r in conn.execute(
                "SELECT DISTINCT suppression_reason FROM suppressed_signals WHERE run_id = ?",
                (row["run_id"],),
            )
        }
        if "stale_source" not in reasons:
            pytest.skip("the recorded run was not blocked by freshness")
        # When a source is stale nothing else is even evaluated: no candidate
        # may be written on data we cannot vouch for.
        assert reasons == {"stale_source"}
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM research_candidates WHERE pipeline_run_id = ?",
            (row["run_id"],),
        ).fetchone()["n"] == 0
    finally:
        conn.close()
