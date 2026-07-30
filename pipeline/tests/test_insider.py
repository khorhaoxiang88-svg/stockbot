"""Form 4 parsing, plan status, supersede semantics, and the scored set."""

import pytest

import migrate
from insider.ingest import link_amendments
from insider.parser import (
    PLAN_CONFIRMED,
    PLAN_DISCRETIONARY,
    PLAN_UNKNOWN,
    SOURCE_ABSENT,
    SOURCE_CHECKBOX,
    SOURCE_FOOTNOTE,
    parse_form4,
)
from universe import identity


def build_form4(
    *, doc_type="4", checkbox=None, footnote=None, nonderiv=(), deriv=(),
    owner_cik="0001111111", owner_name="Doe John", officer=True, director=False,
    period="2026-05-13",
):
    """Minimal but structurally faithful Form 4 XML."""
    checkbox_xml = f"<aff10b5One>{checkbox}</aff10b5One>" if checkbox is not None else ""
    footnote_xml = (
        f"<footnotes><footnote id=\"F1\">{footnote}</footnote></footnotes>" if footnote else ""
    )

    def transactions(rows, tag):
        out = []
        for code, date, shares, price, after in rows:
            out.append(f"""
              <{tag}>
                <securityTitle><value>Common Stock</value></securityTitle>
                <transactionDate><value>{date}</value></transactionDate>
                <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
                <transactionAmounts>
                  <transactionShares><value>{shares}</value></transactionShares>
                  <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
                </transactionAmounts>
                <postTransactionAmounts>
                  <sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction>
                </postTransactionAmounts>
              </{tag}>""")
        return "".join(out)

    return f"""<?xml version="1.0"?>
    <ownershipDocument>
      <documentType>{doc_type}</documentType>
      <periodOfReport>{period}</periodOfReport>
      {checkbox_xml}
      <issuer><issuerCik>0001520006</issuerCik><issuerTradingSymbol>MTDR</issuerTradingSymbol></issuer>
      <reportingOwner>
        <reportingOwnerId>
          <rptOwnerCik>{owner_cik}</rptOwnerCik><rptOwnerName>{owner_name}</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
          <isDirector>{1 if director else 0}</isDirector>
          <isOfficer>{1 if officer else 0}</isOfficer>
          <isTenPercentOwner>0</isTenPercentOwner>
          <officerTitle>Chief Financial Officer</officerTitle>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>{transactions(nonderiv, "nonDerivativeTransaction")}</nonDerivativeTable>
      <derivativeTable>{transactions(deriv, "derivativeTransaction")}</derivativeTable>
      {footnote_xml}
    </ownershipDocument>"""


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "insider.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def insert(conn, accession, line_no, *, code="P", table="I", security_id=None,
           insider_cik="0001111111", is_amendment=0, amends=None, date="2026-05-13",
           filed="2026-05-15", plan="discretionary", source="checkbox", shares=100.0):
    conn.execute(
        """
        INSERT INTO insider_transactions
            (accession_no, line_no, security_id, insider_cik, insider_name, table_type,
             transaction_code, transaction_date, filed_date, plan_status,
             plan_status_source, shares, is_amendment, amends_accession)
        VALUES (?, ?, ?, ?, 'Doe John', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (accession, line_no, security_id, insider_cik, table, code, date, filed,
         plan, source, shares, is_amendment, amends),
    )


# ------------------------------------------------------------- 1. supersede


def test_amendment_supersedes_the_original_without_deleting_it(conn):
    security_id = identity.create_security(
        conn, name="Matador", cik="0001520006", security_type="common_stock",
        classification_confidence="high", classification_source="test",
        first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )
    insert(conn, "0001520006-26-000001", 1, security_id=security_id, shares=100.0)
    # amends_accession starts NULL: a 4/A does not carry the original's
    # accession, so link_amendments has to derive it.
    insert(conn, "0001520006-26-000009", 1, security_id=security_id, shares=150.0,
           is_amendment=1, amends=None, filed="2026-06-01")

    linked = link_amendments(conn)
    assert linked == 1

    all_rows = conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0]
    assert all_rows == 2, "the original must be retained, not deleted"

    effective = conn.execute(
        "SELECT accession_no, shares FROM effective_insider_transactions"
    ).fetchall()
    assert len(effective) == 1, "queries must return one row, not two"
    assert effective[0]["accession_no"] == "0001520006-26-000009"
    assert effective[0]["shares"] == 150.0

    original = conn.execute(
        "SELECT superseded_by_accession FROM insider_transactions "
        "WHERE accession_no = '0001520006-26-000001'"
    ).fetchone()
    assert original["superseded_by_accession"] == "0001520006-26-000009"

    amendment = conn.execute(
        "SELECT amends_accession FROM insider_transactions "
        "WHERE accession_no = '0001520006-26-000009'"
    ).fetchone()
    assert amendment["amends_accession"] == "0001520006-26-000001"


def test_superseded_rows_are_excluded_from_the_scored_set(conn):
    insert(conn, "acc-original", 1, code="P", shares=100.0)
    insert(conn, "acc-amend", 1, code="P", shares=150.0, is_amendment=1,
           amends=None, filed="2026-06-01")
    link_amendments(conn)

    scored = conn.execute("SELECT accession_no FROM scored_insider_purchases").fetchall()
    assert len(scored) == 1
    assert scored[0]["accession_no"] == "acc-amend"


def test_an_amendment_may_have_an_unknown_original(conn):
    """A 4/A carries dateOfOriginalSubmission, not the original's accession.

    When the original is outside the ingested window there is no honest value to
    store, so NULL is allowed. Migration 006 forbade this and forced the ingest
    to write a self-reference; migration 007 removed that constraint.
    """
    conn.execute(
        """
        INSERT INTO insider_transactions
            (accession_no, line_no, table_type, is_amendment, amends_accession)
        VALUES ('acc-x', 1, 'I', 1, NULL)
        """
    )
    row = conn.execute(
        "SELECT amends_accession FROM insider_transactions WHERE accession_no='acc-x'"
    ).fetchone()
    assert row["amends_accession"] is None


def test_an_amendment_may_never_point_at_itself(conn):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO insider_transactions
                (accession_no, line_no, table_type, is_amendment, amends_accession)
            VALUES ('acc-y', 1, 'I', 1, 'acc-y')
            """
        )


# ---------------------------------------------- 2. Table II never scored


def test_table_two_purchase_is_stored_but_never_scored(conn):
    insert(conn, "acc-1", 1, code="P", table="I", shares=100.0)
    insert(conn, "acc-1", 2, code="P", table="II", shares=999.0)

    stored = conn.execute(
        "SELECT COUNT(*) FROM insider_transactions WHERE transaction_code='P'"
    ).fetchone()[0]
    assert stored == 2, "Table II must still be stored"

    scored = conn.execute("SELECT table_type, shares FROM scored_insider_purchases").fetchall()
    assert len(scored) == 1
    assert scored[0]["table_type"] == "I"
    assert scored[0]["shares"] == 100.0


def test_parser_separates_table_one_and_table_two():
    xml = build_form4(
        nonderiv=[("P", "2026-05-13", 100, 10.0, 500)],
        deriv=[("P", "2026-05-13", 200, 1.5, 900)],
    )
    form = parse_form4(xml, "acc-1")
    tables = {row.table_type for row in form.rows}
    assert tables == {"I", "II"}
    assert [r.line_no for r in form.rows] == [1, 2]
    table_one = [r for r in form.rows if r.table_type == "I"][0]
    assert table_one.shares == 100.0 and table_one.price_per_share == 10.0
    assert table_one.total_value == 1000.0


# -------------------------------------------------- 3. plan status is never guessed


def test_missing_checkbox_and_no_footnote_yields_unknown():
    form = parse_form4(build_form4(checkbox=None, nonderiv=[("P", "2018-01-02", 10, 5.0, 20)]))
    assert form.plan_status == PLAN_UNKNOWN
    assert form.plan_status_source == SOURCE_ABSENT
    assert form.plan_status != PLAN_DISCRETIONARY


def test_checked_box_is_confirmed_10b5_1():
    form = parse_form4(build_form4(checkbox="1", nonderiv=[("S", "2026-05-13", 10, 5.0, 20)]))
    assert form.plan_status == PLAN_CONFIRMED
    assert form.plan_status_source == SOURCE_CHECKBOX


def test_unchecked_box_is_discretionary_by_evidence_not_default():
    form = parse_form4(build_form4(checkbox="0", nonderiv=[("P", "2026-05-13", 10, 5.0, 20)]))
    assert form.plan_status == PLAN_DISCRETIONARY
    assert form.plan_status_source == SOURCE_CHECKBOX


def test_footnote_mentioning_a_plan_is_confirmed_via_footnote():
    form = parse_form4(build_form4(
        checkbox=None, footnote="Sale effected pursuant to a Rule 10b5-1 trading plan.",
        nonderiv=[("S", "2019-01-02", 10, 5.0, 20)],
    ))
    assert form.plan_status == PLAN_CONFIRMED
    assert form.plan_status_source == SOURCE_FOOTNOTE


def test_footnote_denying_a_plan_is_not_read_as_confirmation():
    form = parse_form4(build_form4(
        checkbox=None, footnote="This transaction was not made pursuant to a Rule 10b5-1 plan.",
        nonderiv=[("P", "2019-01-02", 10, 5.0, 20)],
    ))
    assert form.plan_status == PLAN_DISCRETIONARY
    assert form.plan_status_source == SOURCE_FOOTNOTE


def test_unrelated_footnote_still_yields_unknown():
    form = parse_form4(build_form4(
        checkbox=None, footnote="Represents restricted stock units granted on June 11.",
        nonderiv=[("A", "2019-01-02", 10, 0.0, 20)],
    ))
    assert form.plan_status == PLAN_UNKNOWN
    assert form.plan_status_source == SOURCE_ABSENT


def test_database_forbids_unknown_with_a_determined_source(conn):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO insider_transactions (accession_no, line_no, table_type, "
            "plan_status, plan_status_source) VALUES ('a', 1, 'I', 'unknown', 'checkbox')"
        )


def test_database_forbids_a_determined_status_with_absent_source(conn):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO insider_transactions (accession_no, line_no, table_type, "
            "plan_status, plan_status_source) VALUES ('a', 1, 'I', 'confirmed_10b5_1', 'absent')"
        )


# ------------------------------------- 4. grants and exercises are not purchases


@pytest.mark.parametrize("code", ["A", "M", "S", "F", "G", "C", "D"])
def test_non_purchase_codes_never_enter_the_scored_set(conn, code):
    insert(conn, f"acc-{code}", 1, code=code, table="I", shares=500.0)
    scored = conn.execute("SELECT COUNT(*) FROM scored_insider_purchases").fetchone()[0]
    assert scored == 0, f"code {code} must not count as a purchase"


def test_only_code_p_on_table_one_is_scored(conn):
    insert(conn, "acc-p", 1, code="P", table="I", shares=100.0)
    insert(conn, "acc-a", 1, code="A", table="I", shares=999.0)
    insert(conn, "acc-m", 1, code="M", table="I", shares=999.0)
    insert(conn, "acc-s", 1, code="S", table="I", shares=999.0)
    insert(conn, "acc-p2", 1, code="P", table="II", shares=999.0)

    rows = conn.execute("SELECT accession_no FROM scored_insider_purchases").fetchall()
    assert [r["accession_no"] for r in rows] == ["acc-p"]


def test_grant_is_parsed_and_kept_but_distinguishable():
    form = parse_form4(build_form4(checkbox="0", nonderiv=[("A", "2026-06-11", 1000, 0.0, 5000)]))
    assert len(form.rows) == 1
    assert form.rows[0].transaction_code == "A"
    assert form.rows[0].table_type == "I"


# ------------------------------------------------------------------ metadata


def test_roles_and_officer_title_are_captured():
    form = parse_form4(build_form4(officer=True, director=True,
                                   nonderiv=[("P", "2026-05-13", 1, 1.0, 1)]))
    assert form.role_officer is True
    assert form.role_director is True
    assert form.role_ten_percent is False
    assert form.officer_title == "Chief Financial Officer"


def test_amendment_flag_is_detected_from_document_type():
    assert parse_form4(build_form4(doc_type="4/A", checkbox="0")).is_amendment is True
    assert parse_form4(build_form4(doc_type="4", checkbox="0")).is_amendment is False


def test_total_value_is_shares_times_price():
    form = parse_form4(build_form4(checkbox="0",
                                   nonderiv=[("P", "2026-05-13", 250, 43.21, 1000)]))
    assert form.rows[0].total_value == pytest.approx(250 * 43.21)


def test_missing_price_leaves_total_value_null():
    xml = build_form4(checkbox="0", nonderiv=[("P", "2026-05-13", 250, "", 1000)])
    form = parse_form4(xml)
    assert form.rows[0].price_per_share is None
    assert form.rows[0].total_value is None
