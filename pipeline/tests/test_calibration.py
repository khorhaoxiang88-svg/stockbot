"""S3 calibration: candidate-rate simulation respects the real rule, and no
return data ever enters this module."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import migrate
from calibration import report as C
from selection import rules as R
from universe import identity

CFG = {
    "horizons": [20, 60],
    "max_open_positions_per_horizon": 100,
    "dilution_disqualify": 22,
    "max_candidates_per_selection": 5,
    "max_per_cohort": 2,
    "exit_cooldown_days": 10,
    "gap_cancel_cooldown_days": 3,
}


def make_row(security_id, cohort_id="SIC-X", composite=80.0, rankable=True, quality=80.0):
    return R.Row(
        security_id=security_id, symbol=f"S{security_id}", cohort_id=cohort_id,
        rankable=rankable, model_applicable=True, composite=composite, rank=security_id,
        quality=quality, inputs_complete=1, dilution_score=0.0, dilution_disqualified=False,
        high_going_concern=False,
    )


# ---------------------------------------------- 1. simulation respects the rule


def test_higher_threshold_never_yields_more_candidates():
    rows = [make_row(i, composite=40 + i) for i in range(1, 20)]
    results = {r["threshold"]: r["candidates_per_week"] for r in C.simulate_candidate_rate(rows, CFG)}
    counts = [results[t] for t in sorted(results)]
    assert all(a >= b for a, b in zip(counts, counts[1:])), (
        "raising the threshold must never increase the candidate count"
    )


def test_simulation_respects_the_max_candidates_cap():
    rows = [make_row(i, cohort_id=f"SIC-{i}", composite=95.0) for i in range(1, 20)]
    results = C.simulate_candidate_rate(rows, CFG)
    for result in results:
        assert result["candidates_per_week"] <= CFG["max_candidates_per_selection"]


def test_simulation_respects_the_per_cohort_cap():
    rows = [make_row(i, cohort_id="SIC-SAME", composite=95.0) for i in range(1, 10)]
    results = C.simulate_candidate_rate(rows, CFG)
    for result in results:
        assert result["candidates_per_week"] <= CFG["max_per_cohort"]


def test_non_rankable_securities_are_never_selected():
    rows = [make_row(1, composite=None, rankable=False)]
    results = C.simulate_candidate_rate(rows, CFG)
    assert all(r["candidates_per_week"] == 0 for r in results)


def test_a_dilution_disqualified_security_is_never_selected():
    row = make_row(1, composite=95.0)
    row = R.Row(**{**row.__dict__, "dilution_disqualified": True})
    results = C.simulate_candidate_rate([row], CFG)
    assert all(r["candidates_per_week"] == 0 for r in results)


# --------------------------------------------------- 2. no return data, anywhere


FORBIDDEN_TOKENS = (
    "exit_price", "gross_pnl", "net_pnl", "pnl_pct", "paper_positions",
    "benchmark_positions", "position_events", "cancelled_entries",
)


def test_report_module_never_references_return_or_execution_data():
    """Checks actual code -- identifiers and SQL string literals -- not the
    module's own docstrings/comments, which legitimately name these tables to
    say they are NOT used."""
    source = Path(C.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    signal = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            signal.add(node.id)
        if isinstance(node, ast.Attribute):
            signal.add(node.attr)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if "SELECT" in text or "FROM " in text or "JOIN" in text:
                signal.add(text)  # SQL only, not prose docstrings
    lowered = " ".join(signal).lower()
    for token in FORBIDDEN_TOKENS:
        assert token not in lowered, f"calibration report must never reference {token!r}"


def test_estimated_weeks_uses_only_config_and_candidate_rate_not_execution_history():
    """The 'estimated time to 100 closed' arithmetic must be derivable from
    just the horizon (a frozen protocol parameter) and the candidate rate --
    never from an actual closed-position count or duration."""
    rows = [make_row(i, cohort_id=f"SIC-{i}", composite=95.0) for i in range(1, 3)]
    results = C.simulate_candidate_rate(rows, CFG)
    result = results[0]
    n = result["candidates_per_week"]
    for horizon in CFG["horizons"]:
        expected = round(100 / n + horizon / 5, 1)
        assert result["estimated_weeks_to_100_closed"][horizon] == expected


# --------------------------------- 3. load_rows: unknown, never zero-filled
#
# Regression: load_rows used to default a security with no dilution_signals or
# risk_flags row to dilution_score=0.0 / high_going_concern=False, which reads
# as "checked, clean" -- exactly the zero-fill this codebase's rule 5 forbids.
# It never entered `rows` -- for the S1/S2 pool, that meant every one of 887
# unscreened securities was silently treated as risk-free in the S3 threshold
# sweep. It must instead be excluded and reported separately as unknown.


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "calibration.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def seed_security(conn, symbol, cik="0000000001"):
    security_id = identity.create_security(
        conn, name=f"{symbol} Inc.", cik=cik, security_type="common_stock",
        classification_confidence="high", classification_source="test",
        first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )
    identity.add_listing(
        conn, security_id=security_id, symbol=symbol, exchange="Nasdaq", valid_from="2020-01-01"
    )
    return security_id


def seed_score(conn, security_id, score_date="2026-08-03"):
    # value=quality=momentum=100, insider=0, dilution_penalty=0 -> composite=90.0
    # exactly, satisfying the DB CHECK tying composite_score to the formula.
    conn.execute(
        'INSERT INTO scores (security_id, score_date, strategy_version, config_hash, '
        'mapping_version, value_score, quality_score, momentum_score, insider_bonus, '
        'composite_score, "rank", cohort_id, rankable, explanation_json) '
        "VALUES (?, ?, 1, 'h', '1', 100.0, 100.0, 100.0, 0.0, 90.0, 1, 'SIC-D', 1, '{}')",
        (security_id, score_date),
    )


def seed_dilution(conn, security_id, as_of="2026-08-03"):
    conn.execute(
        "INSERT INTO dilution_signals (security_id, as_of_date, d1_capacity, d2_issuance, "
        "d3_structural, d4_realised, dilution_score, is_disqualified) "
        "VALUES (?, ?, 0, 0, 0, 0, 0.0, 0)",
        (security_id, as_of),
    )


def seed_risk_flag(conn, security_id, as_of="2026-08-03", severity="none"):
    is_unknown = 1 if severity == "unknown" else 0
    conn.execute(
        "INSERT INTO risk_flags (security_id, as_of_date, flag_code, severity, "
        "evidence_text, source_accession, is_unknown) "
        "VALUES (?, ?, 'going_concern', ?, 'test', ?, ?)",
        (security_id, as_of, severity, None if is_unknown else "acc-test", is_unknown),
    )


def test_fully_screened_security_is_included(conn):
    sid = seed_security(conn, "AAAA")
    seed_score(conn, sid)
    seed_dilution(conn, sid)
    seed_risk_flag(conn, sid)

    rows, excluded = C.load_rows(conn, "2026-08-03")
    assert [r.security_id for r in rows] == [sid]
    assert excluded == []


def test_missing_dilution_signals_is_excluded_not_defaulted_to_clean(conn):
    sid = seed_security(conn, "AAAA")
    seed_score(conn, sid)
    seed_risk_flag(conn, sid)
    # No seed_dilution call: dilution/compute.py never ran for this security.

    rows, excluded = C.load_rows(conn, "2026-08-03")
    assert rows == [], "an unscreened security must never enter rows as if clean"
    assert excluded[0]["security_id"] == sid
    assert excluded[0]["missing"] == ["dilution_signals"]


def test_missing_risk_flags_entirely_is_excluded_not_defaulted_to_clean(conn):
    sid = seed_security(conn, "AAAA")
    seed_score(conn, sid)
    seed_dilution(conn, sid)
    # No seed_risk_flag call: riskflags/compute.py never ran for this security.

    rows, excluded = C.load_rows(conn, "2026-08-03")
    assert rows == []
    assert excluded[0]["missing"] == ["risk_flags"]


def test_missing_both_reports_both(conn):
    sid = seed_security(conn, "AAAA")
    seed_score(conn, sid)

    rows, excluded = C.load_rows(conn, "2026-08-03")
    assert rows == []
    assert excluded[0]["missing"] == ["dilution_signals", "risk_flags"]


def test_risk_flags_present_but_none_high_severity_is_still_included(conn):
    """Checked-and-clean is not the same as never-checked. A security with
    only non-high severities on file must not be excluded."""
    sid = seed_security(conn, "AAAA")
    seed_score(conn, sid)
    seed_dilution(conn, sid)
    seed_risk_flag(conn, sid, severity="none")

    rows, excluded = C.load_rows(conn, "2026-08-03")
    assert [r.security_id for r in rows] == [sid]
    assert excluded == []


def test_simulate_candidate_rate_logs_excluded_as_suppressed_never_as_candidates():
    known = make_row(1, cohort_id="SIC-Y", composite=95.0)
    unknown_item = {
        "security_id": 2, "symbol": "S2", "composite": 99.0, "rank": 1,
        "missing": ["dilution_signals"],
    }
    results = C.simulate_candidate_rate([known], CFG, [unknown_item])
    for result in results:
        assert result["candidates_per_week"] == 1, (
            "the excluded-unknown security must never be counted as a candidate, "
            "however high its composite score"
        )
        assert result["excluded_unknown_dilution_or_risk"] == 1
        # Both horizons in CFG get a suppression row for the excluded security.
        assert result["suppressed"] >= len(CFG["horizons"])
