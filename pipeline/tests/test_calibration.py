"""S3 calibration: candidate-rate simulation respects the real rule, and no
return data ever enters this module."""

from __future__ import annotations

import ast
from pathlib import Path

from calibration import report as C
from selection import rules as R

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
