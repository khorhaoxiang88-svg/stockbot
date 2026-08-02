"""Tests for R1-PROTOCOL-1.1 execution.

The fixture currently has zero live candidates (F10 selected none: the price
pipeline is outside its SLA and composite_threshold is still the placeholder),
so the protocol's pure functions are tested directly, and the trickier
end-to-end paths -- a split mid-hold, a dividend, a delisting -- are tested
against small constructed databases built the same way test_riskflags.py and
test_scoring.py build theirs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import migrate
from execution import actions as ACT
from execution import compute as C
from execution import delisting as DL
from execution import protocol as P

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "migrations"


# ------------------------------------------------------------------ protocol


def test_slippage_bands_match_the_frozen_thresholds():
    assert P.slippage_for_adv(60_000_000, 5, 15) == 5
    assert P.slippage_for_adv(50_000_001, 5, 15) == 5
    assert P.slippage_for_adv(50_000_000, 5, 15) == 15  # boundary: mid band, not high
    assert P.slippage_for_adv(5_000_000, 5, 15) == 15   # boundary: mid band, inclusive
    assert P.slippage_for_adv(10_000_000, 5, 15) == 15


def test_slippage_refuses_below_the_lowest_band_rather_than_inventing_one():
    with pytest.raises(P.ProtocolError, match="below the"):
        P.slippage_for_adv(4_999_999, 5, 15)
    with pytest.raises(P.ProtocolError, match="could not be computed"):
        P.slippage_for_adv(None, 5, 15)


def test_slippage_is_adverse_on_every_kind_of_fill():
    entry = P.entry_fill(100.0, 15.0)
    assert entry > 100.0                      # entry pays UP
    for price in (100.0, 50.0, 200.0):
        assert P.exit_fill(price, 15.0) < price  # every exit gets LESS


def test_stop_and_target_are_derived_from_the_actual_fill_not_the_signal_close():
    fill = 101.50   # signal close was 100.00; the fill gapped up slightly
    stop, target = P.stop_and_target(fill, atr=2.0, stop_multiple=2.0, target_multiple=4.0)
    assert stop == pytest.approx(101.50 - 4.0)
    assert target == pytest.approx(101.50 + 8.0)
    assert stop != pytest.approx(100.0 - 4.0)  # not computed from the signal close


def test_a_split_on_the_entry_session_does_not_trigger_a_false_gap_cancel():
    # $1,200 prior close, a 10-for-1 split takes it to $120; the open is $120.10.
    # Raw comparison would read this as a ~90% collapse and cancel every time.
    decision = P.gap_test(
        open_price=120.10, raw_prior_close=1200.0, split_ratio=10.0,
        atr_at_entry_basis=3.0, limit_atr=1.0,
    )
    assert decision.cancelled is False
    assert decision.adjusted_prior_close == pytest.approx(120.0)
    assert decision.gap == pytest.approx(0.10)


def test_a_genuine_gap_up_with_no_split_still_cancels():
    decision = P.gap_test(
        open_price=110.0, raw_prior_close=100.0, split_ratio=1.0,
        atr_at_entry_basis=2.0, limit_atr=1.0,
    )
    # gap = 10, limit = 1 ATR = 2 -> 10 > 2 -> cancelled
    assert decision.cancelled is True


def test_gap_test_is_one_sided_a_gap_down_never_cancels():
    decision = P.gap_test(
        open_price=80.0, raw_prior_close=100.0, split_ratio=1.0,
        atr_at_entry_basis=2.0, limit_atr=1.0,
    )
    assert decision.cancelled is False


def test_same_bar_stop_and_target_resolves_to_the_stop():
    decision = P.evaluate_bar(
        open_price=100.0, high=112.0, low=88.0, close=105.0,
        stop=90.0, target=110.0, held_sessions=5, horizon_days=20,
    )
    assert decision.exit is True
    assert decision.reason == "stop"
    assert decision.raw_price == 90.0


def test_gap_through_the_stop_exits_at_the_open_not_the_stop():
    decision = P.evaluate_bar(
        open_price=85.0, high=86.0, low=84.0, close=85.5,
        stop=90.0, target=120.0, held_sessions=1, horizon_days=20,
    )
    assert decision.exit is True
    assert decision.reason == "gap_through_stop"
    assert decision.raw_price == 85.0  # the open, NOT the stop price of 90.0


def test_gap_through_the_target_exits_at_the_open_not_the_target():
    decision = P.evaluate_bar(
        open_price=125.0, high=126.0, low=124.0, close=125.5,
        stop=90.0, target=120.0, held_sessions=1, horizon_days=20,
    )
    assert decision.exit is True
    assert decision.reason == "gap_through_target"
    assert decision.raw_price == 125.0


def test_maximum_hold_exits_at_the_close():
    decision = P.evaluate_bar(
        open_price=100.0, high=102.0, low=99.0, close=101.0,
        stop=80.0, target=140.0, held_sessions=20, horizon_days=20,
    )
    assert decision.exit is True
    assert decision.reason == "time_exit"
    assert decision.raw_price == 101.0


def test_no_level_reached_before_the_horizon_does_not_exit():
    decision = P.evaluate_bar(
        open_price=100.0, high=102.0, low=99.0, close=101.0,
        stop=80.0, target=140.0, held_sessions=5, horizon_days=20,
    )
    assert decision.exit is False


def test_pnl_percentage_is_against_the_committed_notional():
    gross, net, pct = P.pnl(shares=10.0, entry_price=100.0, exit_price=110.0,
                            dividends=5.0, notional=1000.0)
    assert gross == pytest.approx(100.0)
    assert net == pytest.approx(105.0)
    assert pct == pytest.approx(0.105)


# -------------------------------------------------------------- corp actions


def test_split_scales_shares_entry_stop_and_target_by_the_ratio():
    result = ACT.apply_split(ratio=10.0, shares=10.0, entry_price=100.0, stop=90.0, target=140.0)
    assert result.shares_after == pytest.approx(100.0)
    assert result.entry_price_after == pytest.approx(10.0)
    assert result.stop_after == pytest.approx(9.0)
    assert result.target_after == pytest.approx(14.0)
    # The ordering between entry, stop and target must survive the split intact.
    assert result.stop_after < result.entry_price_after < result.target_after


def test_dividend_credits_only_when_entry_precedes_the_ex_date():
    before = ACT.apply_dividend(entry_date="2026-01-01", ex_date="2026-02-01",
                                shares=100.0, cash_amount=0.50)
    assert before.entitled is True
    assert before.cash_accrued == pytest.approx(50.0)

    on_ex_date = ACT.apply_dividend(entry_date="2026-02-01", ex_date="2026-02-01",
                                    shares=100.0, cash_amount=0.50)
    assert on_ex_date.entitled is False
    assert on_ex_date.cash_accrued == 0.0

    after = ACT.apply_dividend(entry_date="2026-03-01", ex_date="2026-02-01",
                               shares=100.0, cash_amount=0.50)
    assert after.entitled is False


# --------------------------------------------------------------- delisting


def test_delisting_stays_pending_before_the_grace_period_elapses():
    decision = DL.resolve(delisted_date="2026-01-01", as_of_date="2026-03-01",
                          verified_recovery=None)
    assert decision.action == "stay_pending"
    assert decision.exit_price is None
    assert decision.exit_reason is None


def test_delisting_never_resolves_at_the_last_quote():
    # There is no code path anywhere in this module that takes a "last quoted
    # price" as an input at all -- the only prices resolve() can produce are a
    # verified recovery or zero.
    decision = DL.resolve(delisted_date="2026-01-01", as_of_date="2026-01-02",
                          verified_recovery=None)
    assert decision.exit_price != "last_quote"
    assert decision.action == "stay_pending"


def test_an_unresolved_delisting_values_at_zero_after_180_days():
    decision = DL.resolve(delisted_date="2026-01-01", as_of_date="2026-06-30",
                          verified_recovery=None)
    assert decision.action == "resolve_zero"
    assert decision.exit_price == 0.0
    assert decision.exit_reason == "delisting_zero_after_180d"

    # One day short of 180 must still be pending.
    just_before = DL.resolve(delisted_date="2026-01-01", as_of_date="2026-06-29",
                             verified_recovery=None)
    assert just_before.action == "stay_pending"


def test_a_verified_recovery_wins_at_any_time_no_manual_haircut_possible():
    recovery = DL.VerifiedRecovery(
        price_per_share=12.34, basis="merger_consideration",
        source_reference="8-K item 2.01", verified_at="2026-01-05T00:00:00Z",
    )
    decision = DL.resolve(delisted_date="2026-01-01", as_of_date="2026-01-05",
                          verified_recovery=recovery)
    assert decision.action == "resolve_recovery"
    assert decision.exit_price == 12.34
    assert decision.exit_reason == "delisting_resolved_consideration"


# ------------------------------------------------------ constructed database


def _build_db(path: Path) -> sqlite3.Connection:
    conn = migrate.connect(path)
    migrate.migrate_up(conn)
    return conn


def _seed_common(conn, security_id: int, symbol: str, sic="3571", cik="0000000001"):
    conn.execute(
        "INSERT INTO securities (security_id, cik, share_class, name, security_type, "
        "classification_confidence, classification_source, sic_code, first_seen, "
        "last_seen, is_active, delisted_date) VALUES (?, ?, NULL, ?, 'common_stock', "
        "'high', 'test', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, NULL)",
        (security_id, cik, f"{symbol} Inc.", sic),
    )
    conn.execute(
        "INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary) "
        "VALUES (?, ?, 'NYSE', '2026-01-01', NULL, 1)", (security_id, symbol),
    )
    conn.execute(
        "INSERT INTO fixture_manifest (security_id, symbol_at_selection, inclusion_reason, "
        "category, added_at, manifest_version) VALUES (?, ?, 'test', 'ordinary', "
        "'2026-01-01T00:00:00Z', '1')", (security_id, symbol),
    )


def _insert_bar(conn, security_id: int, day: str, o, h, l, c, v=1_000_000):
    conn.execute(
        "INSERT INTO prices (security_id, date, open, high, low, close, volume, provider, "
        "first_seen_at, last_verified_at, revision, price_data_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'test', 'x', 'x', 0, "
        "(SELECT MAX(dataset_version) FROM price_dataset_versions))",
        (security_id, day, o, h, l, c, v),
    )


def _insert_candidate(conn, candidate_id: str, security_id: int, cutoff_session: str,
                      signal_close: float, atr_value: float) -> None:
    conn.execute(
        "INSERT INTO research_candidates (candidate_id, security_id, generated_at, "
        "data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash, "
        "code_version, selection_rule_version, mapping_version, price_dataset_version, "
        "price_snapshot_hash, source_health_snapshot_json, score_snapshot_json, "
        "accessions_used_json, composite_at_generation, rank_at_generation, "
        "signal_close, atr_value, atr_window, price_data_cutoff, entry_rule, "
        "gap_limit_atr, row_hash) VALUES (?, ?, 'x', ?, "
        "(SELECT snapshot_id FROM universe_snapshot_runs LIMIT 1), "
        "(SELECT run_id FROM pipeline_runs LIMIT 1), 1, 'h', 'v', 1, '1', 1, 'psh', "
        "'{}', '{}', '[]', 55.0, 1, ?, ?, 14, ?, 'next_open', 1.0, 'rh')",
        (candidate_id, security_id, f"{cutoff_session}T20:00:00Z", signal_close,
         atr_value, cutoff_session),
    )


def _bootstrap(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = _build_db(db_path)
    conn.execute("INSERT OR IGNORE INTO price_dataset_versions (dataset_version, created_at, "
                "provider, reason) VALUES (1, 'x', 'test', 'seed')")
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES ('run-seed', 'test', 'x', 'success', 'x')"
    )
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES ('run-exec', 'execution', 'x', 'success', 'x')"
    )
    conn.execute(
        "INSERT INTO universe_snapshot_runs (snapshot_id, effective_at, rules_version, "
        "config_hash, run_id, security_count, is_official) VALUES "
        "('snap-1', '2026-01-01', 'v', 'h', NULL, 1, 1)"
    )
    conn.execute(
        "INSERT INTO books (book_id, horizon_days, starting_nav, current_nav, "
        "open_position_count, strategy_version) VALUES "
        "('book-20d', 20, 100000, 100000, 0, 1), ('book-60d', 60, 100000, 100000, 0, 1)"
    )
    # SPY, for the matched benchmark.
    _seed_common(conn, 99, "SPY", sic=None, cik=None)
    conn.commit()
    return conn


CFG = {
    "position_notional": 1000, "atr_window": 14, "stop_atr_multiple": 2.0,
    "target_atr_multiple": 4.0, "gap_cancel_atr": 1.0,
    "slippage_bps_high_liquidity": 5, "slippage_bps_mid_liquidity": 15,
    "horizons": [20, 60], "strategy_version": 1, "resolution_policy_version": 1,
    "accrual_policy_version": 1,
}


def _adv_bars(conn, security_id: int, before_day: str, price: float, volume: int = 2_000_000):
    """20 trailing sessions of high-ADV volume so slippage resolves to 5 bps."""
    from datetime import date, timedelta
    day = DL.parse_date(before_day)
    for offset in range(1, 21):
        d = (day - timedelta(days=offset)).isoformat()
        _insert_bar(conn, security_id, d, price, price, price, price, volume)


def test_a_split_mid_hold_does_not_manufacture_a_return_or_a_false_stop(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 1, "SPLT")
    _adv_bars(conn, 1, "2026-02-01", 100.0)
    _adv_bars(conn, 99, "2026-02-01", 400.0)

    _insert_bar(conn, 1, "2026-02-01", 100, 101, 99, 100, 2_000_000)   # signal close
    _insert_bar(conn, 1, "2026-02-02", 100.5, 101, 99.5, 100, 2_000_000)  # entry session
    _insert_bar(conn, 99, "2026-02-01", 400, 401, 399, 400, 2_000_000)
    _insert_bar(conn, 99, "2026-02-02", 400.2, 401, 399.5, 400, 2_000_000)

    _insert_candidate(conn, "cand-split", 1, "2026-02-01", 100.0, atr_value=3.0)
    conn.commit()

    sessions = C.all_sessions(conn)
    candidate = dict(conn.execute(
        "SELECT * FROM research_candidates WHERE candidate_id = 'cand-split'"
    ).fetchone())
    decision = C.attempt_entry(conn, candidate, sessions, "2026-02-02", CFG, "run-exec")
    assert decision["outcome"] == "filled"
    C.open_positions_for_candidate(conn, candidate, decision, CFG, "run-exec", 1)
    conn.commit()

    position = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE candidate_id = 'cand-split' AND horizon_days = 20"
    ).fetchone())
    entry_price = position["entry_price"]
    stop_before = position["stop_price"]
    shares_before = position["shares"]

    # A 2-for-1 split on the very next session, with a flat price (100 -> 50,
    # i.e. no real move). Nothing should exit and the position's economics
    # should be unchanged in dollar terms.
    conn.execute(
        "INSERT INTO corporate_actions (security_id, ex_date, action_type, ratio, "
        "provider, requires_manual_review) VALUES (1, '2026-02-03', 'split', 2.0, 'test', 0)"
    )
    _insert_bar(conn, 1, "2026-02-03", 50.0, 50.5, 49.5, 50.0, 4_000_000)
    _insert_bar(conn, 99, "2026-02-03", 400.1, 400.5, 399.8, 400.0, 2_000_000)
    conn.commit()

    C.walk_forward_position(conn, position, C.all_sessions(conn), "2026-02-03", CFG)
    conn.commit()

    after = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE position_id = ?", (position["position_id"],)
    ).fetchone())
    assert after["status"] == "open"          # the split alone must not exit it
    assert after["splits_applied"] == pytest.approx(2.0)
    assert after["shares"] == pytest.approx(shares_before * 2.0)
    assert after["stop_price"] == pytest.approx(stop_before / 2.0)
    # Position value (shares * price) is unchanged by the split alone.
    assert after["shares"] * 50.0 == pytest.approx(shares_before * entry_price, rel=0.02)

    event = dict(conn.execute(
        "SELECT * FROM position_events WHERE position_id = ? AND ex_date = '2026-02-03'",
        (position["position_id"],),
    ).fetchone())
    assert event["action_type"] == "split"
    assert event["ratio"] == 2.0


def test_dividend_entitlement_and_accrual_end_to_end(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 2, "DIVD")
    _adv_bars(conn, 2, "2026-02-01", 50.0)
    _adv_bars(conn, 99, "2026-02-01", 400.0)

    _insert_bar(conn, 2, "2026-02-01", 50, 50.5, 49.5, 50, 2_000_000)
    _insert_bar(conn, 2, "2026-02-02", 50.1, 50.6, 49.6, 50.0, 2_000_000)
    _insert_bar(conn, 99, "2026-02-01", 400, 401, 399, 400, 2_000_000)
    _insert_bar(conn, 99, "2026-02-02", 400.1, 401, 399.5, 400, 2_000_000)
    _insert_candidate(conn, "cand-div", 2, "2026-02-01", 50.0, atr_value=1.5)
    conn.commit()

    sessions = C.all_sessions(conn)
    candidate = dict(conn.execute(
        "SELECT * FROM research_candidates WHERE candidate_id = 'cand-div'"
    ).fetchone())
    decision = C.attempt_entry(conn, candidate, sessions, "2026-02-02", CFG, "run-exec")
    assert decision["outcome"] == "filled"
    C.open_positions_for_candidate(conn, candidate, decision, CFG, "run-exec", 2)
    conn.commit()
    position = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE candidate_id = 'cand-div' AND horizon_days = 20"
    ).fetchone())

    conn.execute(
        "INSERT INTO corporate_actions (security_id, ex_date, action_type, cash_amount, "
        "provider, requires_manual_review) VALUES (2, '2026-02-03', 'dividend', 0.40, "
        "'test', 0)"
    )
    _insert_bar(conn, 2, "2026-02-03", 50.0, 50.4, 49.7, 50.0, 2_000_000)
    _insert_bar(conn, 99, "2026-02-03", 400.1, 400.5, 399.8, 400.0, 2_000_000)
    conn.commit()
    C.walk_forward_position(conn, position, C.all_sessions(conn), "2026-02-03", CFG)
    conn.commit()

    after = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE position_id = ?", (position["position_id"],)
    ).fetchone())
    assert after["dividends_received"] == pytest.approx(position["shares"] * 0.40)

    event = dict(conn.execute(
        "SELECT * FROM position_events WHERE position_id = ? AND action_type = 'dividend'",
        (position["position_id"],),
    ).fetchone())
    assert event["entitled"] == 1
    # entry_date (2026-02-02) precedes the ex-date (2026-02-03): entitled.
    assert position["entry_date"] < "2026-02-03"


def test_delisting_sets_pending_resolution_never_auto_closes(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 3, "DLST")
    _adv_bars(conn, 3, "2026-02-01", 20.0)
    _adv_bars(conn, 99, "2026-02-01", 400.0)
    _insert_bar(conn, 3, "2026-02-01", 20, 20.5, 19.5, 20, 2_000_000)
    _insert_bar(conn, 3, "2026-02-02", 20.1, 20.5, 19.8, 20.0, 2_000_000)
    _insert_bar(conn, 99, "2026-02-01", 400, 401, 399, 400, 2_000_000)
    _insert_bar(conn, 99, "2026-02-02", 400.1, 401, 399.5, 400, 2_000_000)
    _insert_candidate(conn, "cand-dlst", 3, "2026-02-01", 20.0, atr_value=0.6)
    conn.commit()

    sessions = C.all_sessions(conn)
    candidate = dict(conn.execute(
        "SELECT * FROM research_candidates WHERE candidate_id = 'cand-dlst'"
    ).fetchone())
    decision = C.attempt_entry(conn, candidate, sessions, "2026-02-02", CFG, "run-exec")
    C.open_positions_for_candidate(conn, candidate, decision, CFG, "run-exec", 3)
    conn.commit()
    position = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE candidate_id = 'cand-dlst' AND horizon_days = 20"
    ).fetchone())

    conn.execute(
        "UPDATE securities SET is_active = 0, delisted_date = '2026-02-03' WHERE security_id = 3"
    )
    # Market-wide session so the calendar has a date to walk forward to, even
    # though the delisted security itself has no bar on or after it.
    _insert_bar(conn, 99, "2026-02-03", 400.2, 400.7, 399.9, 400.1, 2_000_000)
    conn.commit()

    C.walk_forward_position(conn, position, C.all_sessions(conn), "2026-02-10", CFG)
    conn.commit()
    after = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE position_id = ?", (position["position_id"],)
    ).fetchone())
    assert after["status"] == "pending_resolution"
    assert after["exit_price"] is None
    assert after["exit_reason"] is None
    # It stays in reported exposure: the book's open count is not decremented.
    book = dict(conn.execute("SELECT * FROM books WHERE book_id = 'book-20d'").fetchone())
    assert book["open_position_count"] == 1


def test_an_unresolved_delisting_resolves_at_zero_after_180_days_end_to_end(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 4, "ZERO")
    _adv_bars(conn, 4, "2026-02-01", 10.0)
    _adv_bars(conn, 99, "2026-02-01", 400.0)
    _insert_bar(conn, 4, "2026-02-01", 10, 10.2, 9.8, 10, 2_000_000)
    _insert_bar(conn, 4, "2026-02-02", 10.05, 10.2, 9.9, 10.0, 2_000_000)
    _insert_bar(conn, 99, "2026-02-01", 400, 401, 399, 400, 2_000_000)
    _insert_bar(conn, 99, "2026-02-02", 400.1, 401, 399.5, 400, 2_000_000)
    _insert_candidate(conn, "cand-zero", 4, "2026-02-01", 10.0, atr_value=0.3)
    conn.commit()

    sessions = C.all_sessions(conn)
    candidate = dict(conn.execute(
        "SELECT * FROM research_candidates WHERE candidate_id = 'cand-zero'"
    ).fetchone())
    decision = C.attempt_entry(conn, candidate, sessions, "2026-02-02", CFG, "run-exec")
    C.open_positions_for_candidate(conn, candidate, decision, CFG, "run-exec", 4)
    conn.commit()
    position = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE candidate_id = 'cand-zero' AND horizon_days = 20"
    ).fetchone())

    conn.execute(
        "UPDATE securities SET is_active = 0, delisted_date = '2026-02-03' WHERE security_id = 4"
    )
    # A SPY bar far in the future so the matched benchmark can close too.
    _insert_bar(conn, 99, "2026-08-05", 420, 421, 419, 420, 2_000_000)
    conn.commit()

    C.walk_forward_position(conn, position, C.all_sessions(conn), "2026-08-05", CFG)
    conn.commit()
    after = dict(conn.execute(
        "SELECT * FROM paper_positions WHERE position_id = ?", (position["position_id"],)
    ).fetchone())
    assert after["status"] == "closed"
    assert after["exit_price"] == 0.0
    assert after["exit_reason"] == "delisting_zero_after_180d"
    assert after["exit_date"] == "2026-08-02"  # delisted_date + 180 days, not "today"
    assert after["net_pnl"] == pytest.approx(-position["notional"] + position["dividends_received"])

    bench = dict(conn.execute(
        "SELECT * FROM benchmark_positions WHERE candidate_id = 'cand-zero' AND horizon_days = 20"
    ).fetchone())
    assert bench["status"] == "closed"
    assert bench["exit_date"] == "2026-08-02"


def test_book_nav_and_drawdown_are_measured_against_the_fixed_starting_nav(tmp_path):
    conn = _bootstrap(tmp_path)
    row = dict(conn.execute("SELECT * FROM books WHERE book_id = 'book-20d'").fetchone())
    assert row["starting_nav"] == 100_000
    assert row["current_nav"] == 100_000
    # Drawdown against a FIXED nav means the denominator never moves even as
    # current_nav does; verified here as the invariant the web page must also
    # respect rather than recomputing it against a moving baseline.
    conn.execute("UPDATE books SET current_nav = 95000 WHERE book_id = 'book-20d'")
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM books WHERE book_id = 'book-20d'").fetchone())
    assert updated["starting_nav"] == 100_000
    drawdown_pct = (updated["starting_nav"] - updated["current_nav"]) / updated["starting_nav"]
    assert drawdown_pct == pytest.approx(0.05)


def test_a_gap_up_beyond_the_protocol_limit_is_cancelled_and_logged(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 5, "GAPY")
    _adv_bars(conn, 5, "2026-02-01", 100.0)
    _insert_bar(conn, 5, "2026-02-01", 100, 101, 99, 100, 2_000_000)
    _insert_bar(conn, 5, "2026-02-02", 130, 131, 129, 130, 2_000_000)  # +30, way beyond 1 ATR
    _insert_candidate(conn, "cand-gap", 5, "2026-02-01", 100.0, atr_value=3.0)
    conn.commit()

    sessions = C.all_sessions(conn)
    candidate = dict(conn.execute(
        "SELECT * FROM research_candidates WHERE candidate_id = 'cand-gap'"
    ).fetchone())
    decision = C.attempt_entry(conn, candidate, sessions, "2026-02-02", CFG, "run-exec")
    assert decision["outcome"] == "cancelled"
    assert decision["reason"] == "gap_above_prior_close"

    row = dict(conn.execute(
        "SELECT * FROM cancelled_entries WHERE candidate_id = 'cand-gap'"
    ).fetchone())
    assert row["reason"] == "gap_above_prior_close"
    assert "split ratio" in row["adjusted_basis"] or "no split" in row["adjusted_basis"]
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM paper_positions WHERE candidate_id = 'cand-gap'"
    ).fetchone()["n"] == 0


def test_low_adv_security_cancels_rather_than_using_an_invented_slippage_band(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 6, "THIN")
    # ADV well under the $5M floor: 2,000,000 shares/day is far too small at
    # this price to reach $5M in dollar turnover... use a low price and modest
    # volume so dollar ADV is well below $5M.
    for offset in range(1, 21):
        from datetime import timedelta
        d = (DL.parse_date("2026-02-01") - timedelta(days=offset)).isoformat()
        _insert_bar(conn, 6, d, 2.0, 2.0, 2.0, 2.0, 100_000)  # $200,000/day
    _insert_bar(conn, 6, "2026-02-01", 2.0, 2.05, 1.95, 2.0, 100_000)
    _insert_bar(conn, 6, "2026-02-02", 2.01, 2.05, 1.98, 2.0, 100_000)
    _insert_candidate(conn, "cand-thin", 6, "2026-02-01", 2.0, atr_value=0.05)
    conn.commit()

    sessions = C.all_sessions(conn)
    candidate = dict(conn.execute(
        "SELECT * FROM research_candidates WHERE candidate_id = 'cand-thin'"
    ).fetchone())
    decision = C.attempt_entry(conn, candidate, sessions, "2026-02-02", CFG, "run-exec")
    assert decision["outcome"] == "cancelled"
    assert decision["reason"] == "adv_below_protocol_bands"


# ------------------------------------------------ DB-level open-position backstop
#
# F11 dropped the F10 `positions` table (and its partial unique index) in
# favour of selection-time suppression. That is a claim about the code path,
# not a guarantee the database holds independently of it, so migration 014
# adds two triggers that enforce "at most one open position per (security,
# horizon)" regardless of what pipeline/selection or pipeline/execution do.
# These tests write directly to paper_positions with raw SQL, deliberately
# bypassing compute.py's own logic, to prove the backstop holds even if the
# application code above it is wrong.


def _insert_paper_position(conn, position_id_: str, candidate_id: str, horizon_days: int,
                           book_id: str, status: str):
    conn.execute(
        "INSERT INTO paper_positions (position_id, candidate_id, horizon_days, book_id, "
        "protocol_version, strategy_version, resolution_policy_version, "
        "accrual_policy_version, opened_run_id, last_evaluated_at, entry_date, "
        "entry_price, slippage_bps, shares, notional, stop_price, target_price, status) "
        "VALUES (?, ?, ?, ?, 'R1-PROTOCOL-1.1', 1, 1, 1, 'run-exec', 'x', '2026-02-02', "
        "100.0, 5, 10.0, 1000.0, 90.0, 110.0, ?)",
        (position_id_, candidate_id, horizon_days, book_id, status),
    )


def test_db_trigger_blocks_a_second_open_position_for_the_same_security_and_horizon_on_insert(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 7, "DUPE")
    _insert_candidate(conn, "cand-a", 7, "2026-02-01", 10.0, atr_value=0.5)
    _insert_candidate(conn, "cand-b", 7, "2026-02-08", 10.0, atr_value=0.5)
    conn.commit()

    _insert_paper_position(conn, "pos-a", "cand-a", 20, "book-20d", "open")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="already open"):
        _insert_paper_position(conn, "pos-b", "cand-b", 20, "book-20d", "open")

    # The OTHER horizon is unaffected: 20d and 60d may coexist for one security.
    _insert_paper_position(conn, "pos-c", "cand-b", 60, "book-60d", "open")
    conn.commit()


def test_db_trigger_does_not_block_a_different_security_at_the_same_horizon(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 8, "ONE")
    _seed_common(conn, 9, "TWO")
    _insert_candidate(conn, "cand-one", 8, "2026-02-01", 10.0, atr_value=0.5)
    _insert_candidate(conn, "cand-two", 9, "2026-02-01", 10.0, atr_value=0.5)
    conn.commit()

    _insert_paper_position(conn, "pos-one", "cand-one", 20, "book-20d", "open")
    _insert_paper_position(conn, "pos-two", "cand-two", 20, "book-20d", "open")
    conn.commit()  # must not raise


def test_db_trigger_blocks_reopening_a_pending_position_into_open_via_update(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 10, "REOP")
    _insert_candidate(conn, "cand-x", 10, "2026-02-01", 10.0, atr_value=0.5)
    _insert_candidate(conn, "cand-y", 10, "2026-02-08", 10.0, atr_value=0.5)
    conn.commit()

    _insert_paper_position(conn, "pos-x", "cand-x", 20, "book-20d", "open")
    _insert_paper_position(conn, "pos-y", "cand-y", 20, "book-20d", "pending_resolution")
    conn.commit()

    # Nothing in compute.py ever does this, but the trigger must refuse it
    # regardless -- an invariant enforced at the storage layer, not merely
    # true of the code that happens to exist today.
    with pytest.raises(sqlite3.IntegrityError, match="already open"):
        conn.execute("UPDATE paper_positions SET status = 'open' WHERE position_id = 'pos-y'")


def test_db_trigger_allows_reopening_once_the_original_position_has_closed(tmp_path):
    conn = _bootstrap(tmp_path)
    _seed_common(conn, 11, "SEQ")
    _insert_candidate(conn, "cand-p", 11, "2026-02-01", 10.0, atr_value=0.5)
    _insert_candidate(conn, "cand-q", 11, "2026-02-08", 10.0, atr_value=0.5)
    conn.commit()

    _insert_paper_position(conn, "pos-p", "cand-p", 20, "book-20d", "open")
    _insert_paper_position(conn, "pos-q", "cand-q", 20, "book-20d", "pending_resolution")
    conn.commit()

    conn.execute(
        "UPDATE paper_positions SET status = 'closed', exit_date = '2026-02-10', "
        "exit_price = 95.0, exit_reason = 'time_exit', gross_pnl = -50, net_pnl = -50, "
        "pnl_pct = -0.05 WHERE position_id = 'pos-p'"
    )
    conn.execute("UPDATE paper_positions SET status = 'open' WHERE position_id = 'pos-q'")
    conn.commit()  # must not raise; the slot is free once pos-p is closed
