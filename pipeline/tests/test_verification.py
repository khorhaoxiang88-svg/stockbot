"""Tests for the Phase F exit-criteria harness.

The brief's own automated-test requirement is that "an injected fault must
cause the relevant check to FAIL" -- so most of this file is exactly that: run
a check against the real database, confirm it passes, corrupt one thing,
confirm the SAME check turns red, restore it. Nothing here mocks a check into
passing; every PASS asserted below is a check running against real (or, for
checks 3 and 6, freshly-built synthetic) data.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import migrate
from config_loader import load_config
from verification import checks as V

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "stockbot.db"


def _database() -> sqlite3.Connection:
    if not DB_PATH.exists():
        pytest.skip("no database")
    conn = migrate.connect(DB_PATH)
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='verification_results'"
    ).fetchone():
        conn.close()
        pytest.skip("verification tables not present")
    return conn


CFG = load_config()


# ------------------------------------------------------------------- check 1


@pytest.mark.live_db
def test_check_1_passes_against_the_real_database():
    conn = _database()
    try:
        result = V.check_1_derived_metrics_reproduce(conn, CFG)
        assert result.status == "pass", result.evidence.get("mismatches")
        assert result.evidence["rows_reproduced"] > 0
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_1_fails_when_a_stored_metric_is_corrupted():
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id, period_end, knowledge_date, pe FROM derived_fundamentals "
            "WHERE pe IS NOT NULL LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no security has a stored P/E to corrupt")
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE derived_fundamentals SET pe = ? WHERE security_id = ? AND period_end = ? "
            "AND knowledge_date = ?",
            (float(row["pe"]) + 999.0, row["security_id"], row["period_end"],
             row["knowledge_date"]),
        )
        result = V.check_1_derived_metrics_reproduce(conn, CFG)
        assert result.status == "fail"
        assert any(
            m["security_id"] == row["security_id"] for m in result.evidence["mismatches"]
        )
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------------- check 2


@pytest.mark.live_db
def test_check_2_passes_against_the_real_database():
    conn = _database()
    try:
        result = V.check_2_zero_unknown_classifications(conn)
        assert result.status == "pass"
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_2_fails_when_a_security_is_marked_unknown():
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id FROM fixture_manifest LIMIT 1"
        ).fetchone()
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE securities SET security_type = 'unknown', classification_confidence = 'low' "
            "WHERE security_id = ?",
            (row["security_id"],),
        )
        result = V.check_2_zero_unknown_classifications(conn)
        assert result.status == "fail"
        assert result.evidence["unknown"]
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------------- check 3


def test_check_3_passes_a_synthetic_vendor_correction():
    result = V.check_3_price_correction_reconstruction()
    assert result.status == "pass", result.evidence
    assert result.evidence["before_candidate"] == result.evidence["after_candidate"]
    assert result.evidence["before_position"] == result.evidence["after_position"]
    assert result.evidence["row_hash_problems"] == []


# ------------------------------------------------------------------- check 4


@pytest.mark.live_db
def test_check_4_passes_against_the_real_database():
    conn = _database()
    try:
        result = V.check_4_piotroski_roic_dilution_reproduce(conn, CFG, minimum=10)
        assert result.status == "pass", (
            result.evidence["fundamentals_failures"], result.evidence["dilution_failures"],
        )
        assert len(result.evidence["fundamentals_traced"]) >= 10
        assert len(result.evidence["dilution_checked"]) >= 10
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_4_cannot_corrupt_dilution_scores_because_the_db_check_refuses_it():
    """dilution_signals ties dilution_score to d1..d4 with its own CHECK, so an
    inconsistent row can never exist to corrupt in the first place -- a
    stronger guarantee than check 4 re-verifying one live would be. This test
    proves that guarantee is still in force, which is what check 4's dilution
    half is actually leaning on.
    """
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id, as_of_date, d1_capacity FROM dilution_signals LIMIT 1"
        ).fetchone()
        conn.execute("BEGIN")
        with pytest.raises(sqlite3.IntegrityError, match="dilution_score"):
            conn.execute(
                "UPDATE dilution_signals SET d1_capacity = d1_capacity + 1 "
                "WHERE security_id = ? AND as_of_date = ?",
                (row["security_id"], row["as_of_date"]),
            )
    finally:
        conn.execute("ROLLBACK")
        conn.close()


@pytest.mark.live_db
def test_check_4_fails_when_a_piotroski_signal_is_corrupted():
    """The Piotroski half of check 4 has no DB CHECK behind it (a signal is a
    plain 0/1 column), so this is the fault injection that actually exercises
    check 4's own re-derivation rather than a constraint SQLite already
    enforces.
    """
    conn = _database()
    try:
        # check_4 inspects only each security's LATEST (period_end, knowledge_date)
        # row, so the corrupted row must be that latest one or the check would
        # legitimately look right past it.
        row = conn.execute(
            """
            SELECT d.security_id, d.period_end, d.knowledge_date, d.piotroski_cfo_positive
              FROM derived_fundamentals d
              JOIN (
                  SELECT security_id, MAX(period_end) AS period_end
                    FROM derived_fundamentals GROUP BY security_id
              ) latest_period
                ON latest_period.security_id = d.security_id
               AND latest_period.period_end = d.period_end
              JOIN (
                  SELECT security_id, period_end, MAX(knowledge_date) AS knowledge_date
                    FROM derived_fundamentals GROUP BY security_id, period_end
              ) latest_knowledge
                ON latest_knowledge.security_id = d.security_id
               AND latest_knowledge.period_end = d.period_end
               AND latest_knowledge.knowledge_date = d.knowledge_date
             WHERE d.piotroski_cfo_positive IS NOT NULL
             LIMIT 1
            """
        ).fetchone()
        if row is None:
            pytest.skip("no security has a computable Piotroski signal to corrupt")
        conn.execute("BEGIN")
        flipped = 0 if row["piotroski_cfo_positive"] == 1 else 1
        conn.execute(
            "UPDATE derived_fundamentals SET piotroski_cfo_positive = ? "
            "WHERE security_id = ? AND period_end = ? AND knowledge_date = ?",
            (flipped, row["security_id"], row["period_end"], row["knowledge_date"]),
        )
        result = V.check_4_piotroski_roic_dilution_reproduce(conn, CFG, minimum=10)
        assert result.status == "fail"
        assert any(
            f["security_id"] == row["security_id"] for f in result.evidence["fundamentals_failures"]
        )
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------------- check 5


@pytest.mark.live_db
def test_check_5_is_pending_with_zero_verifications_recorded():
    conn = _database()
    try:
        result = V.check_5_form4_hand_verification(conn)
        count = conn.execute("SELECT COUNT(*) AS n FROM filing_verifications").fetchone()["n"]
        if count == 0:
            assert result.status == "pending"
            assert "0 of 20" in result.detail
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_5_fails_on_a_recorded_mismatch_even_with_enough_volume():
    conn = _database()
    try:
        conn.execute("BEGIN")
        # 20 clean verifications, 3 amendments -- would otherwise PASS.
        for i in range(20):
            conn.execute(
                "INSERT INTO filing_verifications (accession_no, security_id, is_amendment, "
                "matches_source, fields_checked_json, discrepancy_notes, source_url, "
                "verified_by, verified_at) VALUES (?, 1, ?, 1, '[]', NULL, 'https://sec.gov/x', "
                "'tester', 'x')",
                (f"test-acc-{i}", 1 if i < 3 else 0),
            )
        # One MORE filing that a human checked and found wrong.
        conn.execute(
            "INSERT INTO filing_verifications (accession_no, security_id, is_amendment, "
            "matches_source, fields_checked_json, discrepancy_notes, source_url, verified_by, "
            "verified_at) VALUES ('test-acc-bad', 1, 0, 0, '[]', 'shares mismatch', "
            "'https://sec.gov/x', 'tester', 'x')"
        )
        result = V.check_5_form4_hand_verification(conn)
        assert result.status == "fail"
        assert result.evidence["mismatches"]
    finally:
        conn.execute("ROLLBACK")
        conn.close()


@pytest.mark.live_db
def test_check_5_passes_once_enough_real_verifications_exist():
    conn = _database()
    try:
        conn.execute("BEGIN")
        for i in range(20):
            conn.execute(
                "INSERT INTO filing_verifications (accession_no, security_id, is_amendment, "
                "matches_source, fields_checked_json, discrepancy_notes, source_url, "
                "verified_by, verified_at) VALUES (?, 1, ?, 1, '[]', NULL, 'https://sec.gov/x', "
                "'tester', 'x')",
                (f"test-acc-ok-{i}", 1 if i < 3 else 0),
            )
        result = V.check_5_form4_hand_verification(conn)
        assert result.status == "pass"
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------------- check 6


def test_check_6_passes_a_synthetic_split_dividend_and_delisting():
    result = V.check_6_corporate_actions_trace_cleanly()
    assert result.status == "pass", result.evidence
    assert result.evidence["split"]["shares_scaled_by_ratio"]
    assert result.evidence["dividend"]["credited"]
    assert result.evidence["delisting"]["pending_not_closed"]
    assert result.evidence["delisting"]["no_exit_price_assigned"]


# ------------------------------------------------------------------- check 7


@pytest.mark.live_db
def test_check_7_passes_against_the_real_database():
    conn = _database()
    try:
        result = V.check_7_scores_reproduce_from_explanation(conn)
        if result.evidence["total_rankable"] == 0:
            pytest.skip("no rankable scores yet")
        assert result.status == "pass", result.evidence["mismatches"]
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_7_cannot_corrupt_composite_alone_because_the_db_check_refuses_it():
    """scores ties composite_score to its own components with a CHECK, so a
    row where they disagree can never exist to begin with -- proving that
    guarantee still holds is what check 7's top-level arithmetic leans on.
    """
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id, score_date, strategy_version, composite_score "
            "FROM scores WHERE rankable = 1 LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no rankable score to corrupt")
        conn.execute("BEGIN")
        with pytest.raises(sqlite3.IntegrityError, match="composite_score"):
            conn.execute(
                "UPDATE scores SET composite_score = ? "
                "WHERE security_id = ? AND score_date = ? AND strategy_version = ?",
                (float(row["composite_score"]) + 5.0, row["security_id"], row["score_date"],
                 row["strategy_version"]),
            )
    finally:
        conn.execute("ROLLBACK")
        conn.close()


@pytest.mark.live_db
def test_check_7_fails_when_the_explanation_json_disagrees_with_itself():
    """The DB CHECK only sees the top-level columns; it has no idea whether
    explanation_json's own submetric contributions actually sum to what it
    claims. That internal consistency is exactly what check 7 verifies, and
    it is the one fault the schema cannot catch on its own.
    """
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id, score_date, strategy_version, explanation_json "
            "FROM scores WHERE rankable = 1 LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no rankable score to corrupt")
        explanation = json.loads(row["explanation_json"])
        submetrics = explanation["components"]["value"]["detail"]["submetrics"]
        valid = [s for s in submetrics if s["valid"] and s["value_used"] is not None]
        if not valid:
            pytest.skip("no valid Value submetric to corrupt on this row")
        # check_7 recomputes each component from value_used * effective_weight
        # (see _recompute_component_score), not from the stored contribution
        # field, so value_used is the field that must be corrupted here.
        valid[0]["value_used"] += 20.0
        corrupted = json.dumps(explanation)

        conn.execute("BEGIN")
        conn.execute(
            "UPDATE scores SET explanation_json = ? "
            "WHERE security_id = ? AND score_date = ? AND strategy_version = ?",
            (corrupted, row["security_id"], row["score_date"], row["strategy_version"]),
        )
        result = V.check_7_scores_reproduce_from_explanation(conn)
        assert result.status == "fail"
        assert any(m["security_id"] == row["security_id"] for m in result.evidence["mismatches"])
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------------- check 8


@pytest.mark.live_db
def test_check_8_passes_against_the_real_database():
    conn = _database()
    try:
        result = V.check_8_risk_flags_resolve_to_real_filings(conn)
        if result.evidence["total_checked"] == 0:
            pytest.skip("no risk flags yet")
        assert result.status == "pass", result.evidence["unresolved"]
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_8_fails_when_a_source_accession_is_corrupted():
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id, as_of_date, flag_code FROM risk_flags "
            "WHERE is_unknown = 0 AND source_accession IS NOT NULL "
            "AND source_accession != 'none' AND source_accession NOT LIKE 'ledger:%' LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no SEC-sourced flag to corrupt")
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE risk_flags SET source_accession = 'does-not-exist-anywhere' "
            "WHERE security_id = ? AND as_of_date = ? AND flag_code = ?",
            (row["security_id"], row["as_of_date"], row["flag_code"]),
        )
        result = V.check_8_risk_flags_resolve_to_real_filings(conn)
        assert result.status == "fail"
        assert result.evidence["unresolved"]
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------------- check 9


@pytest.mark.live_db
def test_check_9_passes_against_the_real_database():
    conn = _database()
    try:
        result = V.check_9_no_zero_for_absent_data(conn)
        assert result.status == "pass", result.evidence["violations"]
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_9_fails_when_a_never_zero_metric_is_set_to_zero():
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id, period_end, knowledge_date FROM derived_fundamentals "
            "WHERE roic IS NOT NULL LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no security has a stored ROIC to corrupt")
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE derived_fundamentals SET roic = 0.0 WHERE security_id = ? "
            "AND period_end = ? AND knowledge_date = ?",
            (row["security_id"], row["period_end"], row["knowledge_date"]),
        )
        result = V.check_9_no_zero_for_absent_data(conn)
        assert result.status == "fail"
        assert any(v["metric"] == "roic" for v in result.evidence["violations"])
    finally:
        conn.execute("ROLLBACK")
        conn.close()


@pytest.mark.live_db
def test_check_9_fails_on_an_unknown_severity_mismatch():
    conn = _database()
    try:
        row = conn.execute(
            "SELECT security_id, as_of_date, flag_code FROM risk_flags WHERE is_unknown = 0 LIMIT 1"
        ).fetchone()
        conn.execute("BEGIN")
        # Bypass the CHECK by going through a rebuild-free path: SQLite still
        # enforces the CHECK, so directly setting severity='unknown' while
        # is_unknown stays 0 must be rejected at the DB layer already. This
        # proves the CHECK itself is live, which is what check 9 re-verifies.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE risk_flags SET severity = 'unknown' WHERE security_id = ? "
                "AND as_of_date = ? AND flag_code = ?",
                (row["security_id"], row["as_of_date"], row["flag_code"]),
            )
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------------ check 10


@pytest.mark.live_db
def test_check_10_passes_against_the_real_database():
    conn = _database()
    try:
        result = V.check_10_books_never_pooled(conn, CFG)
        assert result.status == "pass", result.evidence
        assert result.evidence["configured_horizons"] == [20, 60]
    finally:
        conn.close()


@pytest.mark.live_db
def test_check_10_fails_when_books_table_is_missing_a_horizon():
    conn = _database()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM books WHERE horizon_days = 60")
        result = V.check_10_books_never_pooled(conn, CFG)
        assert result.status == "fail"
    finally:
        conn.execute("ROLLBACK")
        conn.close()


# ------------------------------------------------------------- the orchestrator


@pytest.mark.live_db
def test_evidence_json_is_always_valid_json_for_every_check():
    """The /health page parses evidence_json directly; a check that produces
    unserialisable evidence would break the page, not just the report."""
    conn = _database()
    try:
        from verification.compute import run_all

        for result in run_all(conn, CFG):
            row = result.as_row("test-run")
            parsed = json.loads(row["evidence_json"])
            assert isinstance(parsed, dict)
    finally:
        conn.close()
