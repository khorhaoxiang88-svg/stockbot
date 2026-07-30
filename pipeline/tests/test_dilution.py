"""Dilution gates and the frozen formula."""

import pytest

import migrate
from dilution.classify import (
    ATM_PROGRAMME,
    DEBT_OR_STRUCTURED,
    EQUITY_OFFERING,
    SHELF_415,
    UNKNOWN,
    VARIABLE_CONVERTIBLE,
    classify_filing,
    shelf_is_unexpired,
)
from dilution.compute import shares_yoy_growth, split_factor_between
from dilution.score import (
    d1_capacity,
    d2_issuance,
    d3_structural,
    d4_realised,
    score_from_evidence,
)
from universe import identity

DEBT_SHELF = """
    JPMorgan Chase Financial Company LLC. Medium-Term Notes, Series A.
    We may offer and sell notes due from time to time. The notes are our
    unsecured obligations. Principal amount $1,000 per note. Market-linked
    notes may reference the common stock of an unaffiliated company.
"""
EQUITY_TAKEDOWN = """
    Prospectus Supplement. We are offering 12,000,000 Shares of Common Stock.
    Our common stock trades on the NYSE. The selling stockholders may offer
    shares of our common stock from time to time.
"""
ATM_DOC = """
    Prospectus Supplement. We may offer and sell shares of our common stock
    having an aggregate offering price of up to $75,000,000 from time to time
    through our sales agent in an at the market offering under Rule 415(a)(4).
"""
VARIABLE_CONVERTIBLE_DOC = """
    Resale prospectus covering shares of common stock issuable upon conversion
    of convertible notes. The conversion price shall equal a 20% discount to
    the lowest VWAP during the ten trading days preceding conversion, a
    variable conversion price subject to reset provisions.
"""
SHELF_DOC = """
    Registration statement on Form S-3 filed pursuant to Rule 415 under the
    Securities Act, registering shares of common stock for sale from time to time.
"""
AMBIGUOUS_DOC = """
    Prospectus supplement relating to securities to be issued from time to time.
    Terms will be described in a future supplement.
"""


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "dilution.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def make_security(conn, cik="0000000042"):
    return identity.create_security(
        conn, name="Test Corp", cik=cik, security_type="common_stock",
        classification_confidence="high", classification_source="test",
        first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )


def add_shares_fact(conn, cik, period_end, shares, accession):
    # xbrl_facts.payload_id is a real foreign key: facts only exist alongside the
    # preserved payload they came from.
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_payloads
            (payload_id, source, endpoint, identifier, relative_path, content_hash,
             byte_size, fetched_at)
        VALUES ('p1', 'sec', 'companyfacts', 'CIK-test', 'data/raw/sec/x.json.gz',
                'hash', 1, '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO xbrl_facts (payload_id, source_fact_key, cik, taxonomy, concept,
                                unit, context_type, period_end, context_hash, semantic_hash,
                                normalized_numeric_value, accession_no, accepted_at,
                                source_endpoint)
        VALUES ('p1', ?, ?, 'dei', 'EntityCommonStockSharesOutstanding', 'shares',
                'instant', ?, 'c', 's', ?, ?, ?, 'companyfacts')
        """,
        (f"k{period_end}", cik, period_end, shares, accession, f"{period_end}T00:00:00Z"),
    )


# ------------------------------------------------- 1. a pure debt shelf scores 0


def test_debt_offering_awards_zero_equity_points():
    for form in ("424B2", "424B5", "S-3"):
        result = classify_filing(form, DEBT_SHELF)
        assert result.outcome == DEBT_OR_STRUCTURED
        assert result.scores is False, f"{form} debt offering must not score"


def test_structured_note_referencing_common_stock_is_still_debt():
    """A market-linked note names common stock as its reference asset.

    Reading that as an equity offering is the false positive that would
    disqualify a bank for issuing debt.
    """
    result = classify_filing("424B2", DEBT_SHELF)
    assert result.outcome == DEBT_OR_STRUCTURED
    assert result.scores is False


def test_debt_evidence_produces_no_tier_points():
    evidence = [
        {"outcome": DEBT_OR_STRUCTURED, "scores": False, "tier": None},
        {"outcome": DEBT_OR_STRUCTURED, "scores": False, "tier": None},
        {"outcome": DEBT_OR_STRUCTURED, "scores": False, "tier": None},
    ]
    score = score_from_evidence(evidence, None, "2026-07-30")
    assert (score.d1_capacity, score.d2_issuance, score.d3_structural, score.d4_realised) == (
        0.0, 0.0, 0.0, 0.0
    )
    assert score.total == 0.0
    assert score.is_disqualified is False


# ------------------------------------------------- 2. a split is not dilution


def test_two_for_one_split_does_not_register_as_share_growth(conn):
    security_id = make_security(conn)
    add_shares_fact(conn, "0000000042", "2025-06-30", 1_000_000, "acc-1")
    add_shares_fact(conn, "0000000042", "2026-06-30", 2_000_000, "acc-2")
    conn.execute(
        """
        INSERT INTO corporate_actions (security_id, ex_date, action_type, ratio, provider,
                                       requires_manual_review)
        VALUES (?, '2026-01-15', 'split', 2.0, 'test', 0)
        """,
        (security_id,),
    )

    growth, detail = shares_yoy_growth(conn, security_id, "0000000042", "2026-07-30")
    assert growth == pytest.approx(0.0, abs=1e-9), "a 2-for-1 split is not 100% dilution"
    assert detail["split_factor_applied"] == 2.0
    assert d4_realised(growth) == 0.0


def test_genuine_dilution_alongside_a_split_is_still_detected(conn):
    security_id = make_security(conn)
    add_shares_fact(conn, "0000000042", "2025-06-30", 1_000_000, "acc-1")
    # 2-for-1 split would give 2,000,000; 2,600,000 means 30% real dilution.
    add_shares_fact(conn, "0000000042", "2026-06-30", 2_600_000, "acc-2")
    conn.execute(
        "INSERT INTO corporate_actions (security_id, ex_date, action_type, ratio, provider, "
        "requires_manual_review) VALUES (?, '2026-01-15', 'split', 2.0, 'test', 0)",
        (security_id,),
    )
    growth, _ = shares_yoy_growth(conn, security_id, "0000000042", "2026-07-30")
    assert growth == pytest.approx(0.30, abs=1e-9)


def test_spinoff_factor_never_restates_a_share_count(conn):
    """Migration 003 files spin-off factors as 'other' with review required."""
    security_id = make_security(conn)
    conn.execute(
        "INSERT INTO corporate_actions (security_id, ex_date, action_type, ratio, provider, "
        "requires_manual_review) VALUES (?, '2026-01-15', 'other', 1.061, 'test', 1)",
        (security_id,),
    )
    assert split_factor_between(conn, security_id, "2025-01-01", "2026-07-30") == 1.0


# ------------------------------------------------- 3. an expired shelf scores 0


def test_expired_shelf_awards_zero_capacity():
    assert shelf_is_unexpired("2022-01-01", "2026-07-30") is False
    assert shelf_is_unexpired("2024-06-01", "2026-07-30") is True

    expired = [{
        "outcome": SHELF_415, "scores": False, "tier": None, "unexpired": False,
    }]
    assert score_from_evidence(expired, None, "2026-07-30").d1_capacity == 0.0


def test_unexpired_shelf_awards_four():
    live = [{"outcome": SHELF_415, "scores": True, "tier": "D1", "unexpired": True}]
    assert score_from_evidence(live, None, "2026-07-30").d1_capacity == 4.0


# --------------------------------------- 4. ambiguous filing -> unknown, zero


def test_ambiguous_filing_yields_unknown_and_no_points():
    result = classify_filing("424B5", AMBIGUOUS_DOC)
    assert result.outcome == UNKNOWN
    assert result.scores is False

    score = score_from_evidence(
        [{"outcome": UNKNOWN, "scores": False, "tier": None}], None, "2026-07-30"
    )
    assert score.total == 0.0
    assert any("unknown" in note for note in score.notes)


def test_missing_document_text_is_unknown_not_a_penalty():
    for text in (None, "", "   "):
        result = classify_filing("424B5", text)
        assert result.outcome == UNKNOWN
        assert result.scores is False


def test_convertible_without_floating_terms_is_unknown_not_dilutive():
    text = "Convertible notes with a fixed conversion price of $10.00 per share."
    result = classify_filing("424B5", text)
    assert result.outcome == UNKNOWN
    assert result.scores is False


# ------------------------------------------- 5 and 6. the fixture ends of the range


def test_a_diluting_company_is_disqualified():
    evidence = [
        {"outcome": SHELF_415, "scores": True, "tier": "D1", "unexpired": True},
        {"outcome": EQUITY_OFFERING, "scores": True, "tier": "D2"},
        {"outcome": EQUITY_OFFERING, "scores": True, "tier": "D2"},
        {"outcome": ATM_PROGRAMME, "scores": True, "tier": "D3"},
    ]
    score = score_from_evidence(evidence, 0.45, "2026-07-30")
    assert score.d1_capacity == 4.0
    assert score.d2_issuance == 7.0
    assert score.d3_structural == 4.0
    assert score.d4_realised == 12.0
    assert score.total == 27.0
    assert score.is_disqualified is True


def test_a_clean_large_cap_scores_zero():
    score = score_from_evidence([], 0.001, "2026-07-30")
    assert score.total == 0.0
    assert score.is_disqualified is False
    assert score.d4_realised == 0.0


def test_capacity_alone_can_never_disqualify():
    """D1 + D3 max out at 12, well under the threshold of 22."""
    evidence = [
        {"outcome": SHELF_415, "scores": True, "tier": "D1", "unexpired": True},
        {"outcome": VARIABLE_CONVERTIBLE, "scores": True, "tier": "D3"},
    ]
    score = score_from_evidence(evidence, None, "2026-07-30")
    assert score.total == 12.0
    assert score.is_disqualified is False


# ------------------------------------------------------------- formula details


@pytest.mark.parametrize(
    "count,expected", [(0, 0.0), (1, 4.0), (2, 7.0), (3, 10.0), (9, 10.0)]
)
def test_d2_steps(count, expected):
    assert d2_issuance(count) == expected


@pytest.mark.parametrize(
    "growth,expected",
    [(None, 0.0), (-0.20, 0.0), (0.0, 0.0), (0.05, 0.0), (0.10, 12 * (0.05 / 0.35)),
     (0.40, 12.0), (2.0, 12.0)],
)
def test_d4_curve(growth, expected):
    assert d4_realised(growth) == pytest.approx(expected)


def test_d3_takes_the_maximum_not_the_sum():
    assert d3_structural(has_atm=True, has_variable_convertible=True) == 8.0
    assert d3_structural(has_atm=True, has_variable_convertible=False) == 4.0
    assert d3_structural(has_atm=False, has_variable_convertible=True) == 8.0
    assert d3_structural(has_atm=False, has_variable_convertible=False) == 0.0


def test_score_is_capped_at_thirty():
    evidence = [
        {"outcome": SHELF_415, "scores": True, "tier": "D1", "unexpired": True},
        {"outcome": EQUITY_OFFERING, "scores": True, "tier": "D2"},
        {"outcome": EQUITY_OFFERING, "scores": True, "tier": "D2"},
        {"outcome": EQUITY_OFFERING, "scores": True, "tier": "D2"},
        {"outcome": VARIABLE_CONVERTIBLE, "scores": True, "tier": "D3"},
    ]
    score = score_from_evidence(evidence, 1.0, "2026-07-30")
    assert score.d1_capacity + score.d2_issuance + score.d3_structural + score.d4_realised == 34.0
    assert score.total == 30.0


def test_unknown_growth_scores_zero_rather_than_assumed():
    score = score_from_evidence([], None, "2026-07-30")
    assert score.d4_realised == 0.0
    assert any("not assumed" in note or "unavailable" in note for note in score.notes)


# ------------------------------------------------------------- classifications


def test_equity_takedown_classified():
    result = classify_filing("424B5", EQUITY_TAKEDOWN)
    assert result.outcome == EQUITY_OFFERING
    assert result.scores is True


def test_atm_classified():
    result = classify_filing("424B5", ATM_DOC)
    assert result.outcome == ATM_PROGRAMME
    assert result.scores is True


def test_variable_convertible_classified():
    result = classify_filing("424B5", VARIABLE_CONVERTIBLE_DOC)
    assert result.outcome == VARIABLE_CONVERTIBLE
    assert result.scores is True


def test_rule_415_shelf_classified():
    result = classify_filing("S-3", SHELF_DOC)
    assert result.outcome == SHELF_415
    assert result.scores is True


def test_database_rejects_a_score_that_contradicts_the_formula(conn):
    import sqlite3

    security_id = make_security(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO dilution_signals (security_id, as_of_date, d1_capacity, d2_issuance,
                                          d3_structural, d4_realised, dilution_score,
                                          is_disqualified)
            VALUES (?, '2026-07-30', 4, 0, 0, 0, 25, 1)
            """,
            (security_id,),
        )


def test_database_rejects_disqualification_from_capacity_alone(conn):
    import sqlite3

    security_id = make_security(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO dilution_signals (security_id, as_of_date, d1_capacity, d2_issuance,
                                          d3_structural, d4_realised, dilution_score,
                                          is_disqualified)
            VALUES (?, '2026-07-30', 4, 0, 8, 0, 12, 1)
            """,
            (security_id,),
        )
