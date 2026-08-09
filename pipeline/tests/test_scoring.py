"""Tests for the composite score.

The load-bearing test is `test_every_stored_score_reproduces_from_its_explanation`.
Everything else in this file guards one rule; that one re-derives every score in
the database by hand from the JSON that is shown to the user, and fails if the
displayed explanation and the stored number ever drift apart.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import migrate
from scoring import insider as INS
from scoring import momentum as MOM
from scoring import quality as QUAL
from scoring.cohorts import cohort_for_sic
from scoring.compute import (
    MIN_VALID_VALUE_SUBMETRICS,
    VALUE_WEIGHTS,
    assign_ranks,
    universe_decision,
)
from scoring.percentile import (
    RankedMetric,
    WeightedComponent,
    blend,
    blend_weight,
    invert_if_lower_is_better,
    percentile,
)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "stockbot.db"
TOLERANCE = 1e-9


# --------------------------------------------------------------- percentiles


def test_percentile_matches_the_specified_formula_when_there_are_no_ties():
    population = [1.0, 2.0, 3.0, 4.0, 5.0]
    # pct(x, P) = 100 * |{y < x}| / (n - 1)
    assert percentile(1.0, population) == 0.0
    assert percentile(3.0, population) == pytest.approx(50.0)
    assert percentile(5.0, population) == pytest.approx(100.0)


def test_tied_values_receive_identical_mid_rank_percentiles():
    population = [1.0, 2.0, 2.0, 2.0, 5.0]
    first = percentile(2.0, population)
    # Every member of the tie group must get the same number, whatever order the
    # rows arrived in.
    for shuffled in ([2.0, 5.0, 1.0, 2.0, 2.0], [5.0, 2.0, 2.0, 2.0, 1.0]):
        assert percentile(2.0, shuffled) == pytest.approx(first)
    # below = 1, equal = 3, n = 5  ->  100 * (1 + 1) / 4 = 50
    assert first == pytest.approx(50.0)


def test_mid_rank_keeps_ties_inside_the_zero_to_one_hundred_range():
    assert percentile(7.0, [7.0, 7.0, 7.0]) == pytest.approx(50.0)
    assert percentile(9.0, [1.0, 9.0, 9.0]) == pytest.approx(75.0)


def test_population_smaller_than_two_is_unavailable_not_zero():
    assert percentile(5.0, [5.0]) is None
    assert percentile(5.0, []) is None
    # The distinction that matters: unavailable is None, and 0.0 is a real score.
    assert percentile(1.0, [1.0, 2.0]) == 0.0


def test_lower_is_better_metrics_are_inverted_after_ranking():
    assert invert_if_lower_is_better(20.0, "pe") == pytest.approx(80.0)
    assert invert_if_lower_is_better(20.0, "roic") == pytest.approx(20.0)
    assert invert_if_lower_is_better(None, "pe") is None


def test_blend_weight_is_zero_below_the_floor_and_clamped_at_one():
    assert blend_weight(9, 10, 50) == 0.0
    assert blend_weight(10, 10, 50) == pytest.approx(0.2)
    assert blend_weight(50, 10, 50) == pytest.approx(1.0)
    assert blend_weight(80, 10, 50) == pytest.approx(1.0)


def test_blend_uses_the_market_population_when_the_cohort_is_too_thin():
    value, reason = blend(cohort_pct=90.0, market_pct=40.0, w=0.0)
    assert value == pytest.approx(40.0) and reason is None
    value, _ = blend(cohort_pct=90.0, market_pct=40.0, w=0.5)
    assert value == pytest.approx(65.0)


def test_blend_is_unavailable_when_the_population_it_needs_is_missing():
    value, reason = blend(cohort_pct=90.0, market_pct=None, w=0.5)
    assert value is None and "market percentile unavailable" in reason


# ------------------------------------------------------------ renormalisation


def _ranked(metric: str, pct: float | None) -> RankedMetric:
    return RankedMetric(
        metric=metric, raw_value=1.0, valid=pct is not None, percentile=pct,
    )


def test_missing_one_value_submetric_renormalises_and_weights_sum_to_one():
    component = WeightedComponent("value", share=1.0)
    component.add("pe", 0.25, _ranked("pe", 80.0))
    component.add("pb", 0.25, _ranked("pb", 60.0))
    component.add("ev_ebitda", 0.25, _ranked("ev_ebitda", 40.0))
    component.add("fcf_yield", 0.25, _ranked("fcf_yield", None))

    weights = component.effective_weights()
    assert set(weights) == {"pe", "pb", "ev_ebitda"}
    assert sum(weights.values()) == pytest.approx(1.0)
    for weight in weights.values():
        assert weight == pytest.approx(1 / 3)
    assert component.score() == pytest.approx((80 + 60 + 40) / 3)


def test_piotroski_share_is_unchanged_when_a_non_piotroski_metric_is_missing():
    component = WeightedComponent("quality", share=QUAL.NON_PIOTROSKI_SHARE)
    component.fixed.append(
        {"metric": "piotroski_f_score", "weight": QUAL.PIOTROSKI_WEIGHT,
         "value": 100.0 * 7 / 9, "raw_value": 7}
    )
    component.add("roic", 0.20, _ranked("roic", 90.0))
    component.add("interest_coverage", 0.15, _ranked("interest_coverage", 50.0))
    component.add("debt_ebitda", 0.15, _ranked("debt_ebitda", 30.0))
    component.add("current_ratio", 0.10, _ranked("current_ratio", None))

    weights = component.effective_weights()
    # The missing 0.10 is redistributed inside the 0.60 only.
    assert sum(weights.values()) == pytest.approx(QUAL.NON_PIOTROSKI_SHARE)
    assert weights["roic"] == pytest.approx(0.60 * 0.20 / 0.50)
    # Piotroski's share never moves, whatever else is missing.
    assert component.fixed[0]["weight"] == pytest.approx(0.40)
    total = sum(weights.values()) + component.fixed[0]["weight"]
    assert total == pytest.approx(1.0)


def test_momentum_never_renormalises():
    component = WeightedComponent("momentum", share=1.0, renormalise=False)
    component.add("rs_21", 0.15, _ranked("rs_21", 100.0))
    component.add("rs_63", 0.25, _ranked("rs_63", None))
    weights = component.effective_weights()
    # rs_63 is missing and rs_21 does NOT grow to cover it.
    assert weights == {"rs_21": 0.15}


def test_momentum_weights_sum_to_one_and_are_frozen():
    assert sum(MOM.WEIGHTS.values()) == pytest.approx(1.0)
    assert MOM.WEIGHTS == {
        "rs_21": 0.15, "rs_63": 0.25, "rs_126": 0.25, "rs_252": 0.15,
        "range52": 0.10, "trend": 0.05, "volratio": 0.05,
    }


def test_quality_weights_are_frozen_and_sum_with_piotroski_to_one():
    assert QUAL.PIOTROSKI_WEIGHT == 0.40
    assert QUAL.NON_PIOTROSKI_WEIGHTS == {
        "roic": 0.20, "interest_coverage": 0.15, "debt_ebitda": 0.15,
        "current_ratio": 0.10,
    }
    assert QUAL.PIOTROSKI_WEIGHT + sum(QUAL.NON_PIOTROSKI_WEIGHTS.values()) == pytest.approx(1.0)


def test_value_weights_are_frozen_at_a_quarter_each():
    assert VALUE_WEIGHTS == {"pe": 0.25, "pb": 0.25, "ev_ebitda": 0.25, "fcf_yield": 0.25}
    assert MIN_VALID_VALUE_SUBMETRICS == 3


# --------------------------------------------------------------- insider bonus


def _purchase(**overrides) -> dict:
    row = {
        "accession_no": "0000000000-26-000001",
        "insider_cik": "0001111111",
        "insider_name": "DOE JANE",
        "officer_title": None,
        "transaction_date": "2026-07-01",
        "plan_status": "discretionary",
        "shares": 1000.0,
        "total_value": 50_000.0,
        "shares_owned_after": 5000.0,
    }
    row.update(overrides)
    return row


AS_OF = date(2026, 7, 29)


def test_one_insider_with_three_purchases_counts_once_toward_n():
    purchases = [
        _purchase(accession_no="a", transaction_date="2026-07-01"),
        _purchase(accession_no="b", transaction_date="2026-07-10"),
        _purchase(accession_no="c", transaction_date="2026-07-20"),
    ]
    value, detail = INS.cluster_bonus(purchases, AS_OF)
    assert detail["distinct_insiders_N"] == 1
    assert len(detail["per_insider"]) == 1
    assert detail["per_insider"][0]["purchase_count"] == 3
    # N = 1 -> max(0, N - 2) = 0 -> no cluster at all.
    assert value == 0.0


def test_distinct_insiders_build_a_cluster_and_q_i_is_the_max_per_person():
    purchases = [
        _purchase(insider_cik="0000000001", accession_no="a", transaction_date="2026-07-29"),
        _purchase(insider_cik="0000000001", accession_no="b", transaction_date="2026-02-01"),
        _purchase(insider_cik="0000000002", accession_no="c", transaction_date="2026-07-29"),
        _purchase(insider_cik="0000000003", accession_no="d", transaction_date="2026-07-29"),
    ]
    value, detail = INS.cluster_bonus(purchases, AS_OF)
    assert detail["distinct_insiders_N"] == 3
    # Insider 1's older purchase must not lower their q_i; max is taken.
    first = [item for item in detail["per_insider"] if item["insider"].endswith("0000000001")][0]
    assert first["q_i"] == pytest.approx(1.0)
    # 4 * min(1, (3-2)/2) * mean(1,1,1) = 4 * 0.5 * 1 = 2
    assert value == pytest.approx(2.0)


def test_plan_status_sets_credibility_and_age_decays_it():
    assert INS.credibility("discretionary") == 1.00
    assert INS.credibility("unknown") == 0.75
    assert INS.credibility("confirmed_10b5_1") == 0.50
    assert INS.decay(0) == pytest.approx(1.0)
    assert INS.decay(90) == pytest.approx(0.5)
    assert INS.decay(180) == 0.0
    assert INS.decay(400) == 0.0


def test_a_purchase_with_inconsistent_prior_holdings_awards_no_conviction_bonus():
    # Owned 500 afterwards but bought 1000: prior holdings would be -500.
    purchases = [_purchase(shares=1000.0, shares_owned_after=500.0)]
    value, detail = INS.conviction_bonus(purchases, AS_OF)
    assert value == 0.0
    assert detail["candidates"][0]["status"] == "unknown"
    assert "internally inconsistent" in detail["candidates"][0]["reason"]
    assert detail["best"] is None


def test_a_new_position_with_zero_prior_holdings_qualifies():
    purchases = [_purchase(shares=1000.0, shares_owned_after=1000.0,
                           transaction_date="2026-07-29")]
    value, detail = INS.conviction_bonus(purchases, AS_OF)
    assert detail["candidates"][0]["status"] == "qualifies"
    assert value == pytest.approx(2.0)


def test_conviction_needs_more_than_a_quarter_of_prior_holdings():
    # 1000 bought on 9000 prior = 11.1%, below the 25% threshold.
    below = [_purchase(shares=1000.0, shares_owned_after=10_000.0)]
    assert INS.conviction_bonus(below, AS_OF)[0] == 0.0
    # 1000 bought on 1000 prior = 100%.
    above = [_purchase(shares=1000.0, shares_owned_after=2000.0,
                       transaction_date="2026-07-29")]
    assert INS.conviction_bonus(above, AS_OF)[0] == pytest.approx(2.0)


def test_executive_bonus_only_counts_ceo_and_cfo_titles():
    assert INS.is_ceo_or_cfo("Chief Executive Officer")
    assert INS.is_ceo_or_cfo("CFO")
    assert not INS.is_ceo_or_cfo("Chief Marketing Officer")
    assert not INS.is_ceo_or_cfo(None)
    purchases = [
        _purchase(officer_title="Chief Marketing Officer", transaction_date="2026-07-29"),
        _purchase(officer_title="CEO", transaction_date="2026-07-29",
                  insider_cik="0000000009"),
    ]
    value, detail = INS.executive_bonus(purchases, AS_OF)
    assert len(detail["candidates"]) == 1
    assert value == pytest.approx(2.0)


def test_bonus_is_capped_at_ten():
    parts = INS.BonusParts(b1_cluster=4.0, b2_executive=2.0, b3_size=2.0, b4_conviction=2.0)
    assert parts.total == pytest.approx(10.0)
    parts = INS.BonusParts(b1_cluster=4.0, b2_executive=2.0, b3_size=2.0, b4_conviction=2.0)
    assert parts.total <= INS.MAX_BONUS


def test_complete_coverage_with_no_purchases_is_an_observed_zero():
    coverage = INS.assess_coverage(
        attempted=True, run_ok=True, run_finished_at="2026-07-29T00:00:00Z",
        staleness_hours=0.0, sla_hours=48.0, originals_ingested=3,
        amendments_ingested=0, oldest_filed_date="2019-01-01",
        window_start="2026-01-30",
    )
    assert coverage.complete is True
    assert INS.BonusParts().total == 0.0


def test_stale_ingest_makes_coverage_unknown():
    coverage = INS.assess_coverage(
        attempted=True, run_ok=True, run_finished_at="2026-06-01T00:00:00Z",
        staleness_hours=1000.0, sla_hours=48.0, originals_ingested=3,
        amendments_ingested=0, oldest_filed_date="2019-01-01",
        window_start="2026-01-30",
    )
    assert coverage.complete is False
    assert "stale" in coverage.reason


def test_a_truncated_ingest_inside_the_window_makes_coverage_unknown():
    coverage = INS.assess_coverage(
        attempted=True, run_ok=True, run_finished_at="2026-07-29T00:00:00Z",
        staleness_hours=0.0, sla_hours=48.0,
        originals_ingested=INS.INGEST_MAX_ORIGINALS, amendments_ingested=0,
        oldest_filed_date="2026-04-02", window_start="2026-01-30",
    )
    assert coverage.complete is False
    assert "cap" in coverage.reason


def test_a_truncated_ingest_that_still_predates_the_window_is_complete():
    coverage = INS.assess_coverage(
        attempted=True, run_ok=True, run_finished_at="2026-07-29T00:00:00Z",
        staleness_hours=0.0, sla_hours=48.0,
        originals_ingested=INS.INGEST_MAX_ORIGINALS, amendments_ingested=0,
        oldest_filed_date="2022-08-22", window_start="2026-01-30",
    )
    assert coverage.complete is True


# --------------------------------------------------------------- universe


def test_financials_and_reits_are_never_ranked():
    applicable = {1: 0, 2: 1}
    bank = {"security_id": 1, "security_type": "common_stock", "sic_code": "6021"}
    status, reason = universe_decision(bank, applicable)
    assert status == "excluded" and "model not supported" in reason

    reit = {"security_id": 1, "security_type": "common_stock", "sic_code": "6798"}
    assert universe_decision(reit, applicable)[0] == "excluded"

    operating = {"security_id": 2, "security_type": "common_stock", "sic_code": "3571"}
    assert universe_decision(operating, applicable) == ("included", None)


def test_non_common_stock_and_unknown_classification_are_never_ranked():
    applicable = {1: 1}
    for security_type in ("etf", "preferred_share", "warrant", "unit", "adr"):
        row = {"security_id": 1, "security_type": security_type, "sic_code": "3571"}
        assert universe_decision(row, applicable)[0] == "excluded"
    unknown = {"security_id": 1, "security_type": "unknown", "sic_code": "3571"}
    status, reason = universe_decision(unknown, applicable)
    assert status == "excluded" and "unknown" in reason


def test_cohorts_are_sic_derived_and_never_labelled_gics():
    cohort_id, label = cohort_for_sic("3571")
    assert cohort_id == "SIC-D" and "SIC division D" in label
    assert cohort_for_sic("6798")[0] == "SIC-H"
    assert cohort_for_sic("5331")[0] == "SIC-G"
    assert cohort_for_sic(None)[0] == "SIC-UNKNOWN"
    for code in ("1311", "3674", "7372", "4011", None):
        assert "GICS" not in cohort_for_sic(code)[1]


def test_ranks_are_competition_ranked_and_ties_share_a_position():
    rows = [
        {"rankable": 1, "composite_score": 90.0, "security_id": 1},
        {"rankable": 1, "composite_score": 80.0, "security_id": 2},
        {"rankable": 1, "composite_score": 80.0, "security_id": 3},
        {"rankable": 1, "composite_score": 70.0, "security_id": 4},
        {"rankable": 0, "composite_score": None, "security_id": 5},
    ]
    assign_ranks(rows)
    assert [row.get("rank") for row in rows] == [1, 2, 2, 4, None]


# ------------------------------------------------- reproduction from the store


def _database() -> sqlite3.Connection:
    if not DB_PATH.exists():
        pytest.skip("no database; run pipeline/scoring/compute.py first")
    conn = migrate.connect(DB_PATH)
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scores'"
    ).fetchone():
        conn.close()
        pytest.skip("scores table not present")
    return conn


def _recompute_component(detail: dict) -> float:
    """Re-derive a component score from its stored submetric rows alone."""
    return sum(
        item["effective_weight"] * item["value_used"]
        for item in detail["submetrics"]
        if item["valid"] and item["value_used"] is not None
    )


@pytest.mark.live_db
def test_every_stored_score_reproduces_from_its_explanation():
    conn = _database()
    try:
        rows = conn.execute(
            "SELECT security_id, value_score, quality_score, momentum_score, "
            "insider_bonus, dilution_penalty, composite_score, rankable, "
            "explanation_json FROM scores WHERE rankable = 1"
        ).fetchall()
        assert rows, "no rankable scores stored"
        for row in rows:
            explanation = json.loads(row["explanation_json"])
            components = explanation["components"]

            value = _recompute_component(components["value"]["detail"])
            assert value == pytest.approx(row["value_score"], abs=TOLERANCE)
            quality = _recompute_component(components["quality"]["detail"])
            assert quality == pytest.approx(row["quality_score"], abs=TOLERANCE)
            momentum = _recompute_component(components["momentum"]["detail"])
            assert momentum == pytest.approx(row["momentum_score"], abs=TOLERANCE)

            bonus = explanation["insider_bonus"]
            recomputed_bonus = min(10.0, sum(
                bonus[key]["value"]
                for key in ("b1_cluster", "b2_executive", "b3_size", "b4_conviction")
            ))
            assert recomputed_bonus == pytest.approx(row["insider_bonus"], abs=TOLERANCE)

            composite = max(0.0, min(100.0,
                0.30 * value + 0.30 * quality + 0.30 * momentum
                + recomputed_bonus - row["dilution_penalty"]))
            assert composite == pytest.approx(row["composite_score"], abs=TOLERANCE)
    finally:
        conn.close()


@pytest.mark.live_db
def test_stored_effective_weights_sum_to_one_within_every_component():
    conn = _database()
    try:
        for row in conn.execute(
            "SELECT explanation_json FROM scores WHERE rankable = 1"
        ):
            explanation = json.loads(row["explanation_json"])
            for name in ("value", "quality", "momentum"):
                detail = explanation["components"][name]["detail"]
                total = sum(
                    item["effective_weight"] for item in detail["submetrics"]
                    if item["valid"]
                )
                assert total == pytest.approx(1.0, abs=1e-9), name
    finally:
        conn.close()


@pytest.mark.live_db
def test_no_component_weight_is_ever_redistributed_to_another_component():
    conn = _database()
    try:
        for row in conn.execute(
            "SELECT explanation_json FROM scores WHERE rankable = 1"
        ):
            explanation = json.loads(row["explanation_json"])
            weights = [
                explanation["components"][name]["weight"]
                for name in ("value", "quality", "momentum")
            ]
            assert weights == [0.30, 0.30, 0.30]
            terms = {t["term"]: t for t in explanation["composite"]["terms"]}
            assert terms["0.30 * Value"]["weight"] == 0.30
            assert terms["0.30 * Quality"]["weight"] == 0.30
            assert terms["0.30 * Momentum"]["weight"] == 0.30
    finally:
        conn.close()


@pytest.mark.live_db
def test_piotroski_carries_exactly_four_tenths_in_every_stored_score():
    conn = _database()
    try:
        seen_renormalised = False
        for row in conn.execute(
            "SELECT explanation_json FROM scores WHERE rankable = 1"
        ):
            explanation = json.loads(row["explanation_json"])
            detail = explanation["components"]["quality"]["detail"]
            fixed = [i for i in detail["submetrics"] if i["metric"] == "piotroski_f_score"]
            assert len(fixed) == 1
            assert fixed[0]["effective_weight"] == pytest.approx(0.40)
            others = [i for i in detail["submetrics"] if i["metric"] != "piotroski_f_score"]
            valid = [i for i in others if i["valid"]]
            assert sum(i["effective_weight"] for i in valid) == pytest.approx(0.60)
            if len(valid) < len(others):
                seen_renormalised = True
        # The fixture must actually exercise the renormalisation path.
        assert seen_renormalised
    finally:
        conn.close()


@pytest.mark.live_db
def test_withheld_securities_store_a_reason_and_never_a_score():
    conn = _database()
    try:
        rows = conn.execute(
            "SELECT symbol_or_reason.* FROM ("
            "  SELECT withhold_reason, composite_score, \"rank\", rankable"
            "    FROM scores WHERE rankable = 0) AS symbol_or_reason"
        ).fetchall()
        assert rows, "the fixture is expected to contain withheld securities"
        for row in rows:
            assert row["withhold_reason"]
            assert row["composite_score"] is None
            assert row["rank"] is None
    finally:
        conn.close()


@pytest.mark.live_db
def test_financial_and_reit_securities_are_withheld_in_the_database():
    conn = _database()
    try:
        rows = conn.execute(
            """
            SELECT s.sic_code, sc.rankable, sc.withhold_reason
              FROM scores sc JOIN securities s USING (security_id)
             WHERE s.sic_code IS NOT NULL
               AND CAST(SUBSTR(s.sic_code, 1, 2) AS INTEGER) BETWEEN 60 AND 67
            """
        ).fetchall()
        assert rows, "the fixture contains banks and REITs"
        saw_model_not_supported = False
        for row in rows:
            # The one invariant that must hold for every financial/REIT
            # security regardless of ingest completeness: never ranked.
            # universe_decision() (see test_financials_and_reits_are_never_
            # ranked for the deterministic version of this check) enforces
            # this unconditionally.
            assert row["rankable"] == 0
            # ABR$D is a REIT AND a preferred share. Whichever exclusion fires
            # first, it is never ranked; both reasons are valid answers.
            #
            # "no derived fundamentals at the knowledge cutoff" is a THIRD
            # valid reason here too, distinct from the two above: since S1/S2
            # this table spans the full ~937-security scaled universe, not
            # just the 50-security Phase F fixture, and universe_decision()
            # can only classify model_applicable once a derived_fundamentals
            # row exists (pipeline/scoring/compute.py:model_applicable_map) --
            # a security whose fundamentals haven't been computed yet is
            # correctly withheld for that reason first. That is real,
            # expected ingest-coverage state, not a pipeline bug -- accepting
            # it here is not weakening the invariant above, which every row
            # still satisfies.
            reason = row["withhold_reason"]
            assert (
                "model not supported" in reason
                or "not common stock" in reason
                or "no derived fundamentals" in reason
            )
            saw_model_not_supported |= "model not supported" in reason
        if not saw_model_not_supported:
            pytest.skip(
                "no financial/REIT security in the live database currently has "
                "derived fundamentals computed, so 'model not supported' never "
                "had a chance to fire -- see test_financials_and_reits_are_never_"
                "ranked for the deterministic, always-runs version of this check"
            )
    finally:
        conn.close()


@pytest.mark.live_db
def test_an_observed_zero_bonus_is_still_ranked():
    conn = _database()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM scores WHERE rankable = 1 AND insider_bonus = 0"
        ).fetchone()
        assert row["n"] > 0, "expected at least one ranked security with a zero bonus"
    finally:
        conn.close()


@pytest.mark.live_db
def test_unknown_insider_coverage_withholds_instead_of_scoring_zero():
    conn = _database()
    try:
        rows = conn.execute(
            "SELECT insider_bonus, composite_score FROM scores "
            "WHERE rankable = 0 AND withhold_reason LIKE '%Insider coverage unknown%'"
        ).fetchall()
        assert rows, "expected at least one security withheld for insider coverage"
        for row in rows:
            assert row["insider_bonus"] is None
            assert row["composite_score"] is None
    finally:
        conn.close()
