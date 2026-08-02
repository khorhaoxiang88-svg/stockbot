"""Tests for the risk flags.

The going-concern tests run entirely offline against filing text, because that
is the only way to assert the detector is NARROW. A live fetch would tell us it
fires; only a battery of near-miss passages tells us it does not fire on the
accounting-policy note that appears in thousands of clean filings.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import migrate
from riskflags import altman as ALT
from riskflags import detectors as D
from riskflags import going_concern as GC
from fundamentals.metrics import MISSING, Input

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "stockbot.db"


# --------------------------------------------------------------- going concern

# The construction ASC 205-40 and AS 2415 actually require.
AUDITOR_PARAGRAPH = (
    "<p>The accompanying consolidated financial statements have been prepared "
    "assuming that the Company will continue as a going concern. As discussed in "
    "Note 1 to the consolidated financial statements, the Company has suffered "
    "recurring losses from operations and has a net capital deficiency that raise "
    "substantial doubt about its ability to continue as a going concern. "
    "Management's plans in regard to these matters are also described in Note 1. "
    "The consolidated financial statements do not include any adjustments that "
    "might result from the outcome of this uncertainty.</p>"
)

NOTE_ONE = (
    "<div>NOTE 1 - BASIS OF PRESENTATION AND GOING CONCERN</div><div>The Company "
    "incurred a net loss of $412.6 million for the year ended December 31, 2025 "
    "and had an accumulated deficit of $3.1 billion. These conditions raise "
    "substantial doubt about the Company's ability to continue as a going "
    "concern within one year after the date these financial statements are "
    "issued.</div>"
)

ALLEVIATED = (
    "<div>Management evaluated whether there are conditions or events that raise "
    "substantial doubt about the Company's ability to continue as a going concern. "
    "Management's plans, including the completed refinancing of the revolving "
    "credit facility, alleviate the substantial doubt.</div>"
)

# Near misses. None of these is a going-concern disclosure.
POLICY_DEFINITION = (
    "<p>In accordance with ASC 205-40, management evaluates at each reporting "
    "period whether there are conditions or events that raise substantial doubt "
    "about the entity's ability to continue as a going concern within one year "
    "after the financial statements are issued.</p>"
)
DENIAL = (
    "<p>Management concluded that the conditions described above, considered in "
    "the aggregate, did not raise substantial doubt about the Company's ability "
    "to continue as a going concern.</p>"
)
CLEAN_RISK_FACTOR = (
    "<p>We may require additional capital to fund our operations. If we are "
    "unable to raise capital on acceptable terms, our business, liquidity and "
    "results of operations could be materially harmed, and we may be forced to "
    "curtail or cease operations.</p>"
)
TAGS_SPLIT_THE_WORDS = (
    "<p>...conditions that raise <font style='x'>substantial</font>&nbsp;"
    "<b>doubt</b> about the Company&#8217;s <i>ability to continue as a "
    "going</i> concern.</p>"
)


def scan(html: str) -> GC.GoingConcernMatch:
    return GC.scan_text(GC.extract_text(html))


def test_detector_fires_on_the_standard_auditor_paragraph():
    match = scan(AUDITOR_PARAGRAPH)
    assert match.detected is True
    assert match.alleviated is False
    assert match.offset is not None
    assert "substantial doubt" in match.passage.lower()
    assert "ability to continue as a going concern" in match.passage.lower()


def test_detector_fires_on_the_note_one_disclosure():
    match = scan(NOTE_ONE)
    assert match.detected is True
    assert "accumulated deficit" in match.passage.lower()


def test_detector_does_not_fire_on_a_clean_risk_factor():
    match = scan(CLEAN_RISK_FACTOR)
    assert match.detected is False
    assert match.candidates_examined == 0


def test_detector_does_not_fire_on_the_accounting_policy_definition():
    match = scan(POLICY_DEFINITION)
    assert match.detected is False
    assert "definition" in (match.rejected_reason or "")


def test_detector_does_not_fire_on_an_explicit_denial():
    match = scan(DENIAL)
    assert match.detected is False
    assert "denial" in (match.rejected_reason or "")


def test_alleviated_doubt_is_detected_but_marked_alleviated():
    match = scan(ALLEVIATED)
    # ASC 205-40 doubt that was identified and then alleviated is still a
    # disclosed condition. It fires, and the caller downgrades the severity.
    assert match.detected is True
    assert match.alleviated is True


def test_html_tags_between_the_words_do_not_defeat_the_match():
    match = scan(TAGS_SPLIT_THE_WORDS)
    assert match.detected is True


def test_extract_text_turns_tags_into_word_boundaries():
    assert "goingconcern" not in GC.extract_text("going<br/>concern")
    assert GC.extract_text("going<br/>concern") == "going concern"


def test_a_clean_filing_containing_the_words_far_apart_does_not_fire():
    text = (
        "The Company has substantial doubt regarding the collectability of one "
        "receivable. " + "Filler sentence about inventory. " * 40 +
        "The Company's ability to continue as a going concern is not in question."
    )
    assert GC.scan_text(text).detected is False


class _FakeResponse:
    def __init__(self, payload: bytes, chunk: int):
        self._payload, self._chunk = payload, chunk

    def raise_for_status(self):
        return None

    def iter_content(self, size):  # noqa: ARG002 - the caller's size is ignored
        for start in range(0, len(self._payload), self._chunk):
            yield self._payload[start : start + self._chunk]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSec:
    """Minimal stand-in for SecClient: a rate limiter and a session."""

    def __init__(self, payload: bytes, chunk: int = 64):
        self.limiter = type("L", (), {"acquire": lambda self: None})()
        self.session = type(
            "S", (), {"get": lambda self, url, **kw: _FakeResponse(payload, chunk)}
        )()


def test_stream_scan_finds_a_passage_split_across_chunk_boundaries():
    # 200 KB of filler, then the disclosure, then more filler. With 64-byte
    # chunks the passage is guaranteed to straddle many boundaries.
    payload = (
        "<p>" + ("Ordinary discussion of operations. " * 6000) + "</p>" +
        NOTE_ONE +
        "<p>" + ("More ordinary discussion. " * 3000) + "</p>"
    ).encode("utf-8")
    result = GC.scan_stream(_FakeSec(payload), "http://example.invalid/doc.htm")
    assert result.error is None
    assert result.match is not None
    assert result.match.detected is True
    assert "ability to continue as a going" in result.match.passage.lower()
    # The scan stops as soon as it matches, so it never reads the trailing
    # filler. That is the point of streaming rather than buffering.
    assert 0 < result.bytes_read < len(payload)


def test_stream_scan_has_no_size_limit():
    # Two megabytes of filler ahead of the disclosure. An earlier version capped
    # the read and reported truncation; nothing is capped now.
    payload = (
        "<p>" + ("Filler. " * 260_000) + "</p>" + AUDITOR_PARAGRAPH
    ).encode("utf-8")
    assert len(payload) > 2_000_000
    result = GC.scan_stream(_FakeSec(payload, chunk=8192), "http://example.invalid/big.htm")
    assert result.match is not None and result.match.detected is True


def test_stream_scan_does_not_split_inside_a_tag():
    payload = TAGS_SPLIT_THE_WORDS.encode("utf-8")
    # A chunk size of 7 slices through several tags.
    result = GC.scan_stream(_FakeSec(payload, chunk=7), "http://example.invalid/tags.htm")
    assert result.match is not None and result.match.detected is True


def test_stream_scan_reports_a_clean_document_as_clean_not_unknown():
    payload = (CLEAN_RISK_FACTOR + POLICY_DEFINITION).encode("utf-8")
    result = GC.scan_stream(_FakeSec(payload), "http://example.invalid/clean.htm")
    assert result.error is None
    assert result.match is not None
    assert result.match.detected is False


def test_stream_scan_surfaces_a_transport_error_rather_than_a_clean_result():
    class Broken(_FakeSec):
        def __init__(self):
            super().__init__(b"")
            self.session = type(
                "S", (), {"get": lambda self, url, **kw: (_ for _ in ()).throw(OSError("reset"))}
            )()

    result = GC.scan_stream(Broken(), "http://example.invalid/broken.htm")
    assert result.match is None
    assert "OSError" in result.error


# ----------------------------------------------------------------- Altman Z''


def _altman(**overrides):
    inputs = dict(
        current_assets=150.0, current_liabilities=100.0, retained_earnings=200.0,
        operating_income=80.0, pretax_income=None, interest_expense=None,
        equity=300.0, assets=1000.0, liabilities=700.0, source_accession="a-1",
    )
    inputs.update(overrides)
    return ALT.compute(**inputs)


def test_altman_computes_the_four_variable_z_double_prime():
    result = _altman()
    # X1 = 50/1000, X2 = 200/1000, X3 = 80/1000, X4 = 300/700
    expected = 6.56 * 0.05 + 3.26 * 0.2 + 6.72 * 0.08 + 1.05 * (300 / 700)
    assert result.computable
    assert result.z_double_prime == pytest.approx(expected)
    assert result.z_double_prime == pytest.approx(1.9676, abs=1e-4)
    # 1.97 sits between 1.10 and 2.60, which is the grey zone, not the safe one.
    assert result.zone == "grey"
    assert result.ebit_basis == "operating income as reported"


def test_altman_zone_boundaries():
    assert _altman(operating_income=400.0).zone == "safe"        # Z'' well above 2.60
    assert _altman().zone == "grey"                              # between the two
    assert _altman(retained_earnings=-500.0, operating_income=-80.0,
                   equity=5.0).zone == "distress"                # below 1.10


def test_altman_rebuilds_ebit_from_pretax_plus_interest_when_needed():
    result = _altman(operating_income=None, pretax_income=60.0, interest_expense=20.0)
    assert result.computable
    assert result.ebit_basis == "pretax income + interest expense"
    assert result.terms["inputs"]["ebit"] == pytest.approx(80.0)


def test_altman_is_not_computable_without_retained_earnings():
    result = _altman(retained_earnings=None)
    assert result.computable is False
    assert "retained earnings" in result.missing
    assert result.z_double_prime is None


def test_altman_never_zero_fills_a_missing_input():
    result = _altman(operating_income=None, pretax_income=None, interest_expense=None)
    assert result.computable is False
    assert any("EBIT" in item for item in result.missing)


def test_altman_winsorises_its_inputs_and_records_it():
    # Almost no liabilities: X4 would be 300 without a bound.
    result = _altman(liabilities=1.0)
    assert result.computable
    assert result.terms["x4"]["used"] == ALT.BOUNDS["x4"][1]
    assert any("x4 clamped" in item for item in result.winsorised)


def test_altman_distress_threshold_is_one_point_one():
    assert ALT.DISTRESS_THRESHOLD == 1.10
    result = _altman(retained_earnings=-400.0, operating_income=-50.0, equity=10.0)
    assert result.is_distress is True
    assert result.zone == "distress"


def test_altman_caveat_states_it_is_never_in_the_composite():
    assert "never part of the composite" in ALT.CAVEAT
    assert "not calibrated" in ALT.CAVEAT


# ------------------------------------------------------------------ detectors


def test_negative_operating_cash_flow_fires_and_cites_its_accession():
    flag = D.negative_operating_cash_flow(Input(-500.0, "us-gaap:cfo", "acc-1"), "2025-12-31")
    assert flag.flag_code == "negative_operating_cash_flow"
    assert flag.severity == "high"
    assert flag.source_accession == "acc-1"
    assert flag.is_unknown is False


def test_a_missing_input_produces_an_unknown_not_a_clean_result():
    flag = D.negative_operating_cash_flow(MISSING, "2025-12-31")
    assert flag.is_unknown is True
    assert flag.severity == "unknown"
    assert flag.evidence_text.startswith("Could not determine")
    # An unknown is not the same row a clean check would write.
    clean = D.negative_operating_cash_flow(Input(10.0, "c", "acc-1"), "2025-12-31")
    assert clean.severity == "none"
    assert clean.severity != flag.severity


def test_free_cash_flow_never_assumes_capex_is_zero():
    flag = D.negative_free_cash_flow(Input(100.0, "c", "acc-1"), MISSING, "2025-12-31")
    assert flag.is_unknown is True
    assert "never assumed to be zero" in flag.evidence_text


def test_high_leverage_uses_the_configured_threshold():
    below = D.high_leverage(3.9, "acc-1", 4.0, "2025-12-31")
    assert below.severity == "none"
    above = D.high_leverage(4.1, "acc-1", 4.0, "2025-12-31")
    assert above.severity == "medium"
    far_above = D.high_leverage(7.0, "acc-1", 4.0, "2025-12-31")
    assert far_above.severity == "high"


def test_low_interest_coverage_threshold_is_one_point_five():
    assert D.LOW_INTEREST_COVERAGE == 1.5
    assert D.low_interest_coverage(1.6, "a", 50.0, "2025-12-31").severity == "none"
    assert D.low_interest_coverage(1.4, "a", 50.0, "2025-12-31").severity == "medium"
    assert D.low_interest_coverage(0.8, "a", 50.0, "2025-12-31").severity == "high"


def test_rapid_share_growth_threshold_is_twenty_percent():
    assert D.RAPID_SHARE_GROWTH == 0.20
    detail = {"latest_period": "2025-12-31", "prior_period": "2024-12-31"}
    assert D.rapid_share_growth(0.19, "acc-1", detail).severity == "none"
    assert D.rapid_share_growth(0.25, "acc-1", detail).severity == "medium"
    assert D.rapid_share_growth(1.48, "acc-1", detail).severity == "high"
    assert D.rapid_share_growth(None, "acc-1", {"reason": "no prior count"}).is_unknown


def test_reverse_split_is_detected_and_cites_the_ledger_row():
    flag = D.recent_reverse_split(
        {"ex_date": "2024-08-16", "ratio": 0.1, "provider": "yfinance"}, 42, "2023-07-30"
    )
    assert flag.severity == "medium"
    assert flag.source_accession == "ledger:corporate_actions:42:2024-08-16"
    assert "1-for-10" in flag.evidence_text


def test_insider_selling_is_never_bearish():
    sales = [
        {"accession_no": "acc-1", "insider_name": "Doe Jane", "insider_cik": "1",
         "transaction_date": "2026-07-01", "total_value": 1_000_000.0},
        {"accession_no": "acc-2", "insider_name": "Roe Sam", "insider_cik": "2",
         "transaction_date": "2026-06-01", "total_value": 500_000.0},
    ]
    flag = D.recent_insider_selling(sales, True, "")
    assert flag.severity == "context"
    assert flag.severity not in ("high", "medium", "low")
    assert "CONTEXT ONLY" in flag.evidence_text
    assert "taxes, diversification" in flag.evidence_text
    # And with no sales at all it is still context, never a positive signal.
    assert D.recent_insider_selling([], True, "").severity == "context"


def test_insider_selling_with_incomplete_coverage_is_unknown():
    flag = D.recent_insider_selling([], False, "the F6 ingest cap was reached")
    assert flag.is_unknown is True
    assert flag.severity == "unknown"


def test_stale_data_names_the_fields_and_the_source():
    flag = D.stale_or_incomplete_data(
        [{"source": "sec_companyfacts",
          "detail": "3 field(s) unresolved for period 2025-12-31: cfo, capex, ebitda",
          "blocking": False}],
        "acc-1",
    )
    assert flag.severity == "medium"
    assert "sec_companyfacts" in flag.evidence_text
    assert "cfo, capex, ebitda" in flag.evidence_text


def test_a_failed_source_produces_a_blocking_stale_flag():
    flag = D.stale_or_incomplete_data(
        [{"source": "prices:yfinance",
          "detail": "no price bars at all, so momentum cannot be computed",
          "blocking": True}],
        "acc-1",
    )
    assert flag.severity == "high"
    assert "prices:yfinance" in flag.evidence_text


def test_dilution_flags_are_all_unknown_when_no_filing_was_classified():
    flags = D.dilution_flags([], None)
    assert {f.flag_code for f in flags} == {
        "shelf_capacity", "active_issuance", "atm_or_convertible"
    }
    assert all(f.is_unknown for f in flags)


def test_dilution_flags_carry_the_f7_classification_reason():
    evidence = [
        {"accession": "acc-shelf", "form": "S-3", "filed_date": "2025-01-02",
         "outcome": "shelf_415", "reason": "Rule 415 shelf language", "scores": True,
         "tier": "D1"},
        {"accession": "acc-atm", "form": "424B5", "filed_date": "2025-06-02",
         "outcome": "atm_programme", "reason": "ATM programme tied to common equity",
         "scores": True, "tier": "D2"},
    ]
    flags = {f.flag_code: f for f in D.dilution_flags(evidence, None)}
    assert flags["shelf_capacity"].source_accession == "acc-shelf"
    assert "Rule 415 shelf language" in flags["shelf_capacity"].evidence_text
    assert flags["active_issuance"].source_accession == "acc-atm"
    assert flags["atm_or_convertible"].source_accession == "acc-atm"
    assert "ATM programme tied to common equity" in flags["atm_or_convertible"].evidence_text


# ------------------------------------------------------ against the database


def _database() -> sqlite3.Connection:
    if not DB_PATH.exists():
        pytest.skip("no database; run pipeline/riskflags/compute.py first")
    conn = migrate.connect(DB_PATH)
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='risk_flags'"
    ).fetchone():
        conn.close()
        pytest.skip("risk_flags table not present")
    if not conn.execute("SELECT 1 FROM risk_flags LIMIT 1").fetchone():
        conn.close()
        pytest.skip("no risk flags computed yet")
    return conn


def test_every_non_unknown_flag_has_a_resolvable_source():
    """Resolvable means it points at a row that exists, not merely non-null."""
    conn = _database()
    try:
        rows = conn.execute(
            "SELECT security_id, flag_code, source_accession FROM risk_flags "
            "WHERE is_unknown = 0"
        ).fetchall()
        assert rows, "expected detected flags in the database"
        for row in rows:
            reference = row["source_accession"]
            assert reference, f"{row['flag_code']} has no source"
            if reference == "none":
                # The documented "checked, nothing to point at" sentinel: no
                # sales in the window, no problems to report.
                assert row["flag_code"] in (
                    "recent_insider_selling", "stale_or_incomplete_data"
                )
                continue
            if reference.startswith("ledger:corporate_actions:"):
                _, _, security_id, ex_date = reference.split(":")
                if ex_date == "none":
                    continue
                found = conn.execute(
                    "SELECT 1 FROM corporate_actions WHERE security_id = ? AND ex_date = ?",
                    (int(security_id), ex_date),
                ).fetchone()
                assert found, f"{reference} does not resolve"
                continue
            found = conn.execute(
                "SELECT 1 FROM filings WHERE accession_no = ?", (reference,)
            ).fetchone() or conn.execute(
                "SELECT 1 FROM xbrl_facts WHERE accession_no = ? LIMIT 1", (reference,)
            ).fetchone() or conn.execute(
                "SELECT 1 FROM insider_transactions WHERE accession_no = ? LIMIT 1",
                (reference,),
            ).fetchone()
            assert found, f"{row['flag_code']} cites {reference}, which resolves to nothing"
    finally:
        conn.close()


def test_insider_selling_is_never_bearish_in_the_database():
    conn = _database()
    try:
        rows = conn.execute(
            "SELECT severity FROM risk_flags WHERE flag_code = 'recent_insider_selling'"
        ).fetchall()
        assert rows
        for row in rows:
            assert row["severity"] in ("context", "unknown")
    finally:
        conn.close()


def test_unknowns_carry_the_unknown_severity_and_a_reason():
    conn = _database()
    try:
        rows = conn.execute(
            "SELECT severity, evidence_text, source_accession FROM risk_flags "
            "WHERE is_unknown = 1"
        ).fetchall()
        assert rows, "the fixture is expected to contain unknowns"
        for row in rows:
            assert row["severity"] == "unknown"
            assert row["evidence_text"].startswith("Could not determine")
    finally:
        conn.close()


def test_every_fixture_security_has_a_risk_row_even_when_it_cannot_be_scored():
    conn = _database()
    try:
        missing = conn.execute(
            """
            SELECT f.security_id FROM fixture_manifest f
             WHERE NOT EXISTS (
                SELECT 1 FROM risk_flags r WHERE r.security_id = f.security_id)
            """
        ).fetchall()
        assert not missing, f"securities with no risk flags at all: {[dict(r) for r in missing]}"
    finally:
        conn.close()


def test_a_clean_check_and_an_unknown_are_stored_differently():
    conn = _database()
    try:
        clean = conn.execute(
            "SELECT COUNT(*) AS n FROM risk_flags WHERE severity = 'none'"
        ).fetchone()["n"]
        unknown = conn.execute(
            "SELECT COUNT(*) AS n FROM risk_flags WHERE severity = 'unknown'"
        ).fetchone()["n"]
        assert clean > 0 and unknown > 0
    finally:
        conn.close()
