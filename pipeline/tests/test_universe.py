"""Identity, listings-by-date resolution, and classification."""

import sqlite3

import pytest

import migrate
from universe import identity
from universe.classify import (
    RANKABLE_SECURITY_TYPES,
    Classification,
    classify,
    classify_from_security_title,
    extract_share_class,
    industry_label,
    is_rankable,
    rank_exclusion_reason,
)
from universe.load_fixture import find_existing_security


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "universe.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def make_security(conn, name, cik=None, share_class=None, security_type="common_stock"):
    return identity.create_security(
        conn,
        name=name,
        cik=cik,
        share_class=share_class,
        security_type=security_type,
        classification_confidence="high",
        classification_source="test",
        first_seen="2026-01-01T00:00:00Z",
        last_seen="2026-01-01T00:00:00Z",
    )


# ----------------------------------------------------------- symbol resolution


def test_symbol_resolves_to_correct_security_by_date(conn):
    apple = make_security(conn, "Apple Inc.", cik="0000320193")
    identity.add_listing(
        conn, security_id=apple, symbol="AAPL", exchange="Nasdaq", valid_from="2020-01-01"
    )

    assert identity.resolve_symbol(conn, "AAPL", "2026-07-29") == apple
    assert identity.resolve_symbol(conn, "aapl", "2026-07-29") == apple
    # Before the window opened, nothing held the symbol.
    assert identity.resolve_symbol(conn, "AAPL", "2019-12-31") is None


def test_reused_symbol_resolves_to_two_distinct_security_ids(conn):
    """The whole reason a ticker is not a primary key."""
    first_company = make_security(conn, "First Company Inc.", cik="0000000001")
    second_company = make_security(conn, "Unrelated Company Inc.", cik="0000000002")
    assert first_company != second_company

    # One company holds XYZ, gives it up, another picks it up years later.
    identity.add_listing(
        conn,
        security_id=first_company,
        symbol="XYZ",
        exchange="NYSE",
        valid_from="2005-01-01",
        valid_to="2012-06-30",
    )
    identity.add_listing(
        conn,
        security_id=second_company,
        symbol="XYZ",
        exchange="Nasdaq",
        valid_from="2018-03-01",
    )

    during_first = identity.resolve_symbol(conn, "XYZ", "2010-05-05")
    during_second = identity.resolve_symbol(conn, "XYZ", "2026-07-29")

    assert during_first == first_company
    assert during_second == second_company
    assert during_first != during_second, "a reused symbol must not collapse into one id"

    # The gap between the two windows belongs to nobody.
    assert identity.resolve_symbol(conn, "XYZ", "2015-01-01") is None

    history = identity.symbol_history(conn, "XYZ")
    assert len(history) == 2
    assert {window.security_id for window in history} == {first_company, second_company}


def test_common_stock_and_preferred_share_never_collapse_to_one_security_id(conn):
    """Regression: discovered via Arbor Realty Trust (CIK 0001253986) during S1.

    share_class is NULL for both a company's common stock and its preferred
    series -- classify.extract_share_class only recognises "Class X" wording,
    not preferred series letters like "Series D". Matching an existing
    security on (cik, share_class) alone silently merged ABR common stock
    into the already-loaded ABR$D preferred security_id the first time both
    were loaded under the same CIK. security_type must be part of the match.
    """
    preferred_id = make_security(
        conn,
        "Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock",
        cik="0001253986",
        share_class=None,
        security_type="preferred_share",
    )

    found_without_type = find_existing_security(
        conn, "0001253986", None, "Arbor Realty Trust Common Stock"
    )
    assert found_without_type == preferred_id, (
        "sanity check: without security_type, the old bug reproduces"
    )

    found_with_type = find_existing_security(
        conn, "0001253986", None, "Arbor Realty Trust Common Stock", "common_stock"
    )
    assert found_with_type is None, (
        "a common-stock candidate must not match an existing preferred_share security"
    )

    common_id = make_security(
        conn, "Arbor Realty Trust Common Stock", cik="0001253986",
        share_class=None, security_type="common_stock",
    )
    assert common_id != preferred_id

    # Re-resolving the preferred by its own type still finds the original row.
    assert find_existing_security(
        conn, "0001253986", None, "irrelevant", "preferred_share"
    ) == preferred_id


def test_readding_the_same_current_symbol_does_not_open_a_second_window(conn):
    """Regression: found at S2 real-sample scale. Re-running pool discovery
    on a later date for an already-current symbol (MSFT, PG, CAT, ...) opened
    a second valid_to IS NULL row instead of leaving the first alone, since
    the PK includes valid_from and the two calls used different dates."""
    msft = make_security(conn, "Microsoft Corporation", cik="0000789019")
    identity.add_listing(conn, security_id=msft, symbol="MSFT", exchange="Nasdaq",
                          valid_from="2020-01-01")

    identity.add_listing(conn, security_id=msft, symbol="MSFT", exchange="Nasdaq",
                          valid_from="2026-08-04")

    open_rows = conn.execute(
        "SELECT symbol, valid_from FROM listings WHERE security_id = ? AND valid_to IS NULL",
        (msft,),
    ).fetchall()
    assert len(open_rows) == 1, "re-confirming the same current symbol must not add a second row"
    assert open_rows[0]["valid_from"] == "2020-01-01", "the original window must be left alone"


def test_a_genuine_symbol_change_closes_the_old_window_first(conn):
    company = make_security(conn, "Renamed Co", cik="0000000099")
    identity.add_listing(conn, security_id=company, symbol="OLD", exchange="Nasdaq",
                          valid_from="2020-01-01")

    identity.add_listing(conn, security_id=company, symbol="NEW", exchange="Nasdaq",
                          valid_from="2026-08-04")

    old_row = conn.execute(
        "SELECT valid_to FROM listings WHERE security_id = ? AND symbol = 'OLD'", (company,),
    ).fetchone()
    assert old_row["valid_to"] == "2026-08-04"

    open_rows = conn.execute(
        "SELECT symbol FROM listings WHERE security_id = ? AND valid_to IS NULL", (company,),
    ).fetchall()
    assert [r["symbol"] for r in open_rows] == ["NEW"]


def test_valid_to_is_exclusive(conn):
    security = make_security(conn, "Edge Case Inc.")
    identity.add_listing(
        conn,
        security_id=security,
        symbol="EDGE",
        exchange="NYSE",
        valid_from="2020-01-01",
        valid_to="2020-12-31",
    )
    assert identity.resolve_symbol(conn, "EDGE", "2020-12-30") == security
    assert identity.resolve_symbol(conn, "EDGE", "2020-12-31") is None


def test_delisting_is_soft_so_security_id_is_never_freed(conn):
    security = make_security(conn, "Gone Inc.")
    identity.add_listing(
        conn, security_id=security, symbol="GONE", exchange="NYSE", valid_from="2015-01-01"
    )
    identity.mark_delisted(conn, security, "2024-11-01")

    row = conn.execute(
        "SELECT is_active, delisted_date FROM securities WHERE security_id = ?", (security,)
    ).fetchone()
    assert row[0] == 0
    assert row[1] == "2024-11-01"
    # Row still exists, so AUTOINCREMENT never hands this id to anyone else.
    assert identity.resolve_symbol(conn, "GONE", "2026-07-29") is None
    assert identity.resolve_symbol(conn, "GONE", "2020-01-01") == security


def test_new_security_never_reuses_a_delisted_id(conn):
    first = make_security(conn, "Old Inc.")
    identity.mark_delisted(conn, first, "2024-01-01")
    second = make_security(conn, "New Inc.")
    assert second > first


# ------------------------------------------------------------- classification


ETF_LISTING = {
    "symbol": "SPY",
    "security_name": "State Street SPDR S&P 500 ETF Trust",
    "exchange": "NYSE Arca",
    "is_etf": True,
    "is_test_issue": False,
}
PREFERRED_LISTING = {
    "symbol": "ABR$D",
    "security_name": "Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock",
    "exchange": "NYSE",
    "is_etf": False,
    "is_test_issue": False,
}
WARRANT_LISTING = {
    "symbol": "AACIW",
    "security_name": "Armada Acquisition Corp. III - Warrant",
    "exchange": "Nasdaq",
    "is_etf": False,
    "is_test_issue": False,
}


def test_known_etf_is_classified_etf():
    result = classify(ETF_LISTING)
    assert result.security_type == "etf"
    assert result.confidence == "high"
    assert result.source == "nasdaq:etf_flag"


def test_known_preferred_share_is_classified_preferred():
    result = classify(PREFERRED_LISTING)
    assert result.security_type == "preferred_share"
    assert result.confidence == "high"


def test_known_warrant_is_classified_warrant():
    result = classify(WARRANT_LISTING)
    assert result.security_type == "warrant"
    assert result.confidence == "high"


def test_american_depositary_shares_are_not_read_as_preferred():
    """Regression: 'Depositary' in the ADR name used to win the preferred rule."""
    result = classify(
        {
            "symbol": "ABEV",
            "security_name": "Ambev S.A. American Depositary Shares (Each representing 1 Common Share)",
            "exchange": "NYSE",
            "is_etf": False,
            "is_test_issue": False,
        }
    )
    assert result.security_type == "adr"


def test_spac_unit_is_not_read_as_warrant():
    """Regression: a unit's name describes the warrant inside it."""
    result = classify(
        {
            "symbol": "AAC.U",
            "security_name": (
                "Ares Acquisition Corporation III Units, each consisting of one Class A "
                "ordinary share and one-half of one redeemable warrant"
            ),
            "exchange": "NYSE",
            "is_etf": False,
            "is_test_issue": False,
        }
    )
    assert result.security_type == "unit"


def test_capital_stock_is_common_stock():
    result = classify(
        {
            "symbol": "GOOG",
            "security_name": "Alphabet Inc. - Class C Capital Stock",
            "exchange": "Nasdaq",
            "is_etf": False,
            "is_test_issue": False,
        }
    )
    assert result.security_type == "common_stock"
    assert result.share_class == "C"


def test_bare_class_name_is_medium_confidence_not_unknown():
    result = classify(
        {
            "symbol": "LEN.B",
            "security_name": "Lennar Corporation Class B",
            "exchange": "NYSE",
            "is_etf": False,
            "is_test_issue": False,
        }
    )
    assert result.security_type == "common_stock"
    assert result.confidence == "medium"
    assert result.share_class == "B"


def test_test_issue_flag_wins_over_everything():
    result = classify(
        {
            "symbol": "ZAZZT",
            "security_name": "Nasdaq TEST Common Stock",
            "exchange": "Nasdaq",
            "is_etf": True,
            "is_test_issue": True,
        }
    )
    assert result.security_type == "test_issue"


# -------------------------------------------- unknown never gets high confidence


def test_missing_listing_is_unknown_with_low_confidence():
    result = classify(None)
    assert result.security_type == "unknown"
    assert result.confidence == "low"


def test_unmatched_name_is_unknown_with_low_confidence():
    result = classify(
        {
            "symbol": "ZZZZ",
            "security_name": "Something Entirely Unparseable",
            "exchange": "NYSE",
            "is_etf": False,
            "is_test_issue": False,
        }
    )
    assert result.security_type == "unknown"
    assert result.confidence == "low"


def test_unknown_with_high_confidence_is_impossible_in_code():
    with pytest.raises(ValueError, match="never be high confidence"):
        Classification("unknown", "high", "hand-written")


def test_unknown_with_high_confidence_is_impossible_in_the_database(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO securities (name, security_type, classification_confidence,
                                    classification_source, first_seen, last_seen)
            VALUES ('Sneaky Inc.', 'unknown', 'high', 'test', '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z')
            """
        )


def test_unknown_securities_are_never_rankable():
    assert is_rankable("common_stock", "high") is True
    assert is_rankable("common_stock", "medium") is True
    assert is_rankable("unknown", "low") is False
    assert is_rankable("unknown", "medium") is False


# ------------------------------------------------- non-common-stock exclusion


@pytest.mark.parametrize(
    "security_type",
    ["preferred_share", "warrant", "right", "unit", "etf", "etn",
     "closed_end_fund", "adr", "trust_unit", "test_issue"],
)
def test_non_common_stock_is_never_rankable(security_type):
    """Excluded for what it is, at every confidence level."""
    for confidence in ("high", "medium", "low"):
        assert is_rankable(security_type, confidence) is False


def test_exclusion_is_independent_of_the_unknown_check():
    """A perfectly classified preferred share is still excluded.

    This is the point of the rule: it must not depend on the classification
    having failed, and it must not wait for a later model-applicability flag.
    """
    assert is_rankable("preferred_share", "high") is False
    reason = rank_exclusion_reason("preferred_share", "high")
    assert reason is not None
    assert "not common stock" in reason
    assert "unknown" not in reason


def test_exclusion_reason_names_the_real_cause():
    assert "unknown" in rank_exclusion_reason("unknown", "low")
    assert "not common stock" in rank_exclusion_reason("warrant", "high")
    assert "confidence" in rank_exclusion_reason("common_stock", "low")
    assert rank_exclusion_reason("common_stock", "high") is None
    assert rank_exclusion_reason("common_stock", "medium") is None


def test_the_actual_fixture_members_are_excluded_for_the_right_reason():
    """ABR$D and AACIW are real fixture rows. Tie the rule to them by name."""
    preferred = classify(PREFERRED_LISTING)
    warrant = classify(WARRANT_LISTING)
    etf = classify(ETF_LISTING)

    # All three classified cleanly at high confidence...
    assert preferred.confidence == warrant.confidence == etf.confidence == "high"

    # ...and all three are still barred from ranking, by security type.
    for classification in (preferred, warrant, etf):
        reason = rank_exclusion_reason(
            classification.security_type, classification.confidence
        )
        assert reason is not None
        assert "not common stock" in reason


def test_medium_confidence_common_stock_stays_rankable():
    """LEN.B is medium confidence common stock. It must not get swept up."""
    lennar_b = classify(
        {
            "symbol": "LEN.B",
            "security_name": "Lennar Corporation Class B",
            "exchange": "NYSE",
            "is_etf": False,
            "is_test_issue": False,
        }
    )
    assert lennar_b.confidence == "medium"
    assert is_rankable(lennar_b.security_type, lennar_b.confidence) is True


def test_only_common_stock_is_in_the_rankable_set():
    assert RANKABLE_SECURITY_TYPES == frozenset({"common_stock"})


# ------------------------------------------------------------------ misc rules


def test_delisted_security_classified_from_cover_page_title():
    result = classify_from_security_title("Common Stock, $0.0001 par value SAVE New York Stock Exchange")
    assert result.security_type == "common_stock"
    assert result.confidence == "high"
    assert "Security12bTitle" in result.source


def test_empty_cover_title_is_unknown_low():
    result = classify_from_security_title(None)
    assert result.security_type == "unknown"
    assert result.confidence == "low"


def test_share_class_extraction():
    assert extract_share_class("Alphabet Inc. - Class A Common Stock") == "A"
    assert extract_share_class("Apple Inc. - Common Stock") is None


def test_industry_labels():
    assert industry_label("6021") == "Bank"
    assert industry_label("6798") == "REIT"
    assert industry_label("3571") is None
    assert industry_label(None) is None


def test_delisted_security_must_record_a_date(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO securities (name, security_type, classification_confidence,
                                    classification_source, first_seen, last_seen,
                                    is_active, delisted_date)
            VALUES ('No Date Inc.', 'common_stock', 'high', 'test',
                    '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0, NULL)
            """
        )
