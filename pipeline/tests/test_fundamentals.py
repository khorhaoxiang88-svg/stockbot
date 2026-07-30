"""Validity rules, knowledge states, and provenance."""

import json

import pytest

import migrate
from fundamentals import metrics as M
from fundamentals.mappings import MAPPING_VERSION, seed_concept_mappings
from universe import identity

I = M.Input  # noqa: E741


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "fundamentals.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def make_security(conn, name="Test Corp", cik="0000000042", sic="3571"):
    security_id = identity.create_security(
        conn, name=name, cik=cik, sic_code=sic, security_type="common_stock",
        classification_confidence="high", classification_source="test",
        first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )
    return security_id


def insert_row(conn, security_id, period_end, knowledge_date, **overrides):
    row = {
        "security_id": security_id, "period_end": period_end,
        "knowledge_date": knowledge_date, "fact_set_hash": overrides.pop("hash", "h1"),
        "mapping_version": MAPPING_VERSION, "inputs_complete": 1,
        "model_applicable": 1, "computed_at": "2026-07-30T00:00:00Z",
    }
    row.update(overrides)
    columns = ",".join(row)
    conn.execute(
        f"INSERT INTO derived_fundamentals ({columns}) "
        f"VALUES ({','.join('?' for _ in row)})",
        list(row.values()),
    )


# --------------------------------------------- 1. amendments add, never replace


def test_amendment_creates_a_second_row_and_the_original_survives(conn):
    security_id = make_security(conn)
    insert_row(conn, security_id, "2024-06-30", "2024-08-28T21:00:00Z",
               gross_margin=0.1658, gross_margin_accession="0001628280-16-019274", hash="a")
    insert_row(conn, security_id, "2024-06-30", "2025-05-16T22:49:48Z",
               gross_margin=0.1492, gross_margin_accession="0001375365-19-000039", hash="b")

    rows = conn.execute(
        "SELECT knowledge_date, gross_margin, gross_margin_accession FROM derived_fundamentals "
        "WHERE security_id=? AND period_end='2024-06-30' ORDER BY knowledge_date",
        (security_id,),
    ).fetchall()

    assert len(rows) == 2, "the amendment must not overwrite the original"
    assert rows[0]["gross_margin"] == pytest.approx(0.1658)
    assert rows[1]["gross_margin"] == pytest.approx(0.1492)
    assert rows[0]["gross_margin_accession"] != rows[1]["gross_margin_accession"]
    assert rows[1]["knowledge_date"] > rows[0]["knowledge_date"]


def test_point_in_time_query_returns_the_older_knowledge_state(conn):
    security_id = make_security(conn)
    insert_row(conn, security_id, "2024-06-30", "2024-08-28T21:00:00Z", gross_margin=0.1658, hash="a")
    insert_row(conn, security_id, "2024-06-30", "2025-05-16T22:49:48Z", gross_margin=0.1492, hash="b")

    as_known_then = conn.execute(
        "SELECT gross_margin FROM derived_fundamentals WHERE security_id=? "
        "AND knowledge_date <= '2025-01-01T00:00:00Z' ORDER BY knowledge_date DESC LIMIT 1",
        (security_id,),
    ).fetchone()
    assert as_known_then["gross_margin"] == pytest.approx(0.1658)

    latest = conn.execute(
        "SELECT gross_margin FROM latest_fundamentals WHERE security_id=?", (security_id,)
    ).fetchone()
    assert latest["gross_margin"] == pytest.approx(0.1492)


# ------------------------------------------------- 2. negative earnings -> NULL


def test_negative_earnings_yields_null_pe_not_zero():
    result = M.price_earnings(I(1_000_000.0), I(-2.50), I(30.0), I(-400_000.0))
    assert result.value is None, "a loss-making company must not receive a P/E"
    assert result.reason == "invalid:earnings<=0"


def test_zero_earnings_yields_null_pe():
    assert M.price_earnings(I(1_000.0), I(0.0), I(10.0), I(0.0)).value is None


def test_negative_earnings_never_becomes_a_negative_rankable_number():
    result = M.price_earnings(I(1_000_000.0), M.MISSING, M.MISSING, I(-400_000.0))
    assert result.value is None
    assert result.value != 0


def test_negative_book_value_yields_null_pb():
    result = M.price_book(I(1_000.0), I(-50.0))
    assert result.value is None and result.reason == "invalid:book_value<=0"


def test_negative_ebitda_yields_null_ev_ebitda():
    ebitda = M.ebitda(I(-500.0), I(100.0))
    result = M.ev_to_ebitda(M.Metric(10_000.0), ebitda)
    assert result.value is None and result.reason == "invalid:ebitda<=0"


def test_non_positive_invested_capital_yields_null_roic():
    result = M.roic(I(100.0), I(20.0), I(80.0), I(10.0), I(20.0), I(500.0))
    assert result.value is None and result.reason == "invalid:invested_capital<=0"


# ------------------------------------------------------------- 3. zero-debt case


def test_zero_debt_gives_interest_coverage_at_the_cap():
    # Resolved inputs always carry the tag they came from, so the capped value
    # is still traceable to the fact that proved debt was zero.
    zero_debt = I(0.0, "us-gaap:LongTermDebtNoncurrent", "0000320193-25-000079")
    result = M.interest_coverage(I(500.0), M.MISSING, zero_debt, cap=50.0)
    assert result.value == 50.0
    assert result.concept_used == "us-gaap:LongTermDebtNoncurrent"
    assert result.accession == "0000320193-25-000079"


def test_zero_debt_gives_debt_to_ebitda_of_zero():
    result = M.debt_to_ebitda(I(0.0), M.ebitda(I(500.0), I(100.0)))
    assert result.value == 0.0
    assert result.reason is None


def test_debt_present_but_interest_missing_is_invalid_not_capped():
    result = M.interest_coverage(I(500.0), M.MISSING, I(1_000.0), cap=50.0)
    assert result.value is None
    assert result.reason == "invalid:debt>0_and_interest_missing"


def test_interest_coverage_is_capped():
    result = M.interest_coverage(I(10_000.0), I(1.0), I(500.0), cap=50.0)
    assert result.value == 50.0


def test_current_ratio_is_capped_and_zero_liabilities_is_invalid():
    assert M.current_ratio(I(100.0), I(10.0), cap=5.0).value == 5.0
    invalid = M.current_ratio(I(100.0), I(0.0), cap=5.0)
    assert invalid.value is None and invalid.reason == "invalid:current_liabilities=0"


def test_missing_input_never_becomes_zero():
    for result in (
        M.current_ratio(M.MISSING, I(10.0), 5.0),
        M.gross_margin(M.MISSING, M.MISSING, M.MISSING),
        M.fcf_yield(M.MISSING, I(10.0), I(100.0)),
        M.revenue_growth(I(100.0), M.MISSING),
    ):
        assert result.value is None
        assert result.reason and result.reason.startswith("missing:")


# ------------------------------------------------ 4. model applicability by SIC


@pytest.mark.parametrize(
    "sic,expected_applicable",
    [("6021", False), ("6022", False), ("6798", False), ("6311", False),
     ("6199", False), ("6531", False), ("3571", True), ("7372", True),
     ("1311", True), ("5961", True), (None, True)],
)
def test_model_applicability_by_sic(sic, expected_applicable):
    applicable, reason = M.model_applicable(sic)
    assert applicable is expected_applicable
    if not applicable:
        assert reason


def test_fixture_bank_and_reit_are_not_model_applicable():
    bank_ok, bank_reason = M.model_applicable("6021")   # JPM, USB
    reit_ok, reit_reason = M.model_applicable("6798")   # O, PLD
    assert bank_ok is False and "bank" in bank_reason
    assert reit_ok is False and "REIT" in reit_reason


def test_model_applicable_flag_is_stored(conn):
    bank = make_security(conn, "Bank Corp", "0000019617", "6021")
    reit = make_security(conn, "REIT Corp", "0000726728", "6798")
    operating = make_security(conn, "Widget Corp", "0000320193", "3571")
    for security_id, sic in ((bank, "6021"), (reit, "6798"), (operating, "3571")):
        applicable, _ = M.model_applicable(sic)
        insert_row(conn, security_id, "2025-12-31", "2026-02-01T00:00:00Z",
                   model_applicable=1 if applicable else 0)

    rows = {
        r["security_id"]: r["model_applicable"]
        for r in conn.execute("SELECT security_id, model_applicable FROM derived_fundamentals")
    }
    assert rows[bank] == 0
    assert rows[reit] == 0
    assert rows[operating] == 1


# -------------------------------------------- 5. provenance on every populated value


def test_every_populated_metric_carries_concept_and_accession():
    populated = [
        M.price_earnings(I(1e6), I(2.0, "us-gaap:EarningsPerShareDiluted", "acc-1"), I(40.0), M.MISSING),
        M.price_book(I(1e6, "x", "acc-2"), I(5e5, "us-gaap:StockholdersEquity", "acc-2")),
        M.current_ratio(I(100.0, "us-gaap:AssetsCurrent", "acc-3"), I(50.0, "c", "acc-3"), 5.0),
        M.gross_margin(I(40.0, "us-gaap:GrossProfit", "acc-4"), I(100.0, "r", "acc-4"), M.MISSING),
        M.debt_to_ebitda(I(0.0, "us-gaap:LongTermDebtNoncurrent", "acc-5"),
                         M.ebitda(I(10.0, "o", "acc-5"), I(1.0, "d", "acc-5"))),
        M.interest_coverage(I(100.0, "o", "acc-6"), M.MISSING, I(0.0, "us-gaap:DebtCurrent", "acc-6"), 50.0),
        M.piotroski_cfo_positive(I(10.0, "us-gaap:NetCashProvidedByUsedInOperatingActivities", "acc-7")),
    ]
    for result in populated:
        assert result.value is not None
        assert result.concept_used, f"value {result.value} has no concept_used"
        assert result.accession, f"value {result.value} has no accession"


def test_unavailable_metrics_carry_a_reason_and_no_provenance():
    result = M.price_book(M.MISSING, I(100.0))
    assert result.value is None
    assert result.concept_used is None and result.accession is None
    assert result.reason == "missing:market_cap"


def test_missing_fields_json_lists_every_null(conn):
    security_id = make_security(conn)
    missing = {"pe": "invalid:earnings<=0", "ev_ebitda": "missing:depreciation"}
    insert_row(conn, security_id, "2025-12-31", "2026-02-01T00:00:00Z",
               pe=None, ev_ebitda=None, inputs_complete=0,
               missing_fields_json=json.dumps(missing, sort_keys=True))
    row = conn.execute("SELECT * FROM derived_fundamentals").fetchone()
    stored = json.loads(row["missing_fields_json"])
    assert stored == missing
    assert row["pe"] is None and row["ev_ebitda"] is None
    assert row["inputs_complete"] == 0


# ------------------------------------------------------------- concept mapping


def test_concept_mapping_is_seeded_priority_ordered_and_versioned(conn):
    seed_concept_mappings(conn)
    rows = conn.execute(
        "SELECT concept, priority FROM concept_mappings "
        "WHERE metric_name='revenue' AND mapping_version=? ORDER BY priority",
        (MAPPING_VERSION,),
    ).fetchall()
    assert rows[0]["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert [r["priority"] for r in rows] == sorted(r["priority"] for r in rows)
    assert any(r["concept"] == "SalesRevenueNet" for r in rows), "pre-606 tag must be mapped"


def test_seeding_twice_is_idempotent(conn):
    seed_concept_mappings(conn)
    first = conn.execute("SELECT COUNT(*) FROM concept_mappings").fetchone()[0]
    seed_concept_mappings(conn)
    assert conn.execute("SELECT COUNT(*) FROM concept_mappings").fetchone()[0] == first


def test_input_truthiness_is_blocked():
    """A zero value is present. Truthiness would silently treat it as missing."""
    with pytest.raises(TypeError):
        bool(I(0.0))
