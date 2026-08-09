"""News Ledger, Stage A (migration 024): schema-level proofs.

Three things this file exists to prove, mirroring the project's own existing
discipline for exactly this kind of guarantee:

  1. news_events is append-only and its abstain/amount invariants are CHECK-
     enforced by SQLite itself, not by convention (same pattern as
     test_defect_log.py's paper_positions/defect_log tests).
  2. Nothing in scoring/selection/riskflags/execution ever references a News
     table or view -- an AST + SQL-literal source scan, same technique
     test_calibration.py already uses to prove no return data leaks into the
     calibration report.
  3. v1 (exp-d59006eb199b, strategy_version 2) is exactly as it was before
     this migration existed: same active-experiment row, same frozen config
     digest, same frozen_config_lock row. Baseline values captured directly
     against the real project database immediately after migration 024 was
     applied and before any News code wrote a single row.
"""

from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import migrate

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NEWS_IDENTIFIERS = {
    "news_filings", "news_filing_documents", "news_events", "effective_news_events",
}


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "news_ledger.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


def seed_security(conn, security_id=1, cik="0000000001"):
    conn.execute(
        "INSERT INTO securities (security_id, cik, share_class, name, security_type, "
        "classification_confidence, classification_source, sic_code, first_seen, "
        "last_seen, is_active, delisted_date) VALUES (?, ?, NULL, 'Acme', "
        "'common_stock', 'high', 'test', '3571', 'x', 'x', 1, NULL)",
        (security_id, cik),
    )


def seed_filing(conn, accession_no="0000000001-26-000001", security_id=1, cik="0000000001"):
    conn.execute(
        "INSERT INTO news_filings (accession_no, cik, security_id, form_type, filed_date, "
        "accepted_at, period_of_report, primary_doc_url, payload_id, ingested_at) "
        "VALUES (?, ?, ?, '8-K', '2026-01-01', '2026-01-01T21:00:00Z', NULL, NULL, NULL, 'x')",
        (accession_no, cik, security_id),
    )


EVENT_COLUMNS = (
    "event_id, security_id, accession_no, accepted_at, source_document, extracted_at, "
    "is_abstain, abstain_reason, event_type_candidate, confirmation_tier, amount_explicit, "
    "amount_stated, amount_type, currency, contract_duration_months, annualization_method, "
    "includes_optional_extensions, supporting_passage, passage_source_offset, "
    "extraction_model_version, extraction_prompt_version, supersedes_event_id"
)


def insert_event(
    conn, event_id, accession_no="0000000001-26-000001", security_id=1, is_abstain=0,
    abstain_reason=None, event_type_candidate="binding_commercial_contract",
    confirmation_tier="binding", amount_explicit=1, amount_stated=1_000_000.0,
    supersedes_event_id=None,
):
    if is_abstain:
        event_type_candidate = None
        confirmation_tier = None
    conn.execute(
        f"INSERT INTO news_events ({EVENT_COLUMNS}) VALUES "
        "(?, ?, ?, '2026-01-01T21:00:00Z', 'doc.htm', 'x', ?, ?, ?, ?, ?, ?, NULL, NULL, "
        "NULL, NULL, NULL, 'passage text', NULL, 'claude-sonnet-5', 'news-extract-v1', ?)",
        (
            event_id, security_id, accession_no, is_abstain, abstain_reason,
            event_type_candidate, confirmation_tier, amount_explicit, amount_stated,
            supersedes_event_id,
        ),
    )


# --------------------------------------------------------------- abstain shape


def test_abstain_row_must_carry_no_event_type_or_tier(conn):
    seed_security(conn)
    seed_filing(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            f"INSERT INTO news_events ({EVENT_COLUMNS}) VALUES "
            "('e1', 1, '0000000001-26-000001', 'x', 'doc.htm', 'x', 1, 'no candidate event', "
            "'binding_commercial_contract', NULL, 0, NULL, NULL, NULL, NULL, NULL, NULL, "
            "'p', NULL, 'm', 'v', NULL)"
        )


def test_abstain_row_must_carry_a_reason(conn):
    seed_security(conn)
    seed_filing(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            f"INSERT INTO news_events ({EVENT_COLUMNS}) VALUES "
            "('e1', 1, '0000000001-26-000001', 'x', 'doc.htm', 'x', 1, NULL, NULL, NULL, "
            "0, NULL, NULL, NULL, NULL, NULL, NULL, 'p', NULL, 'm', 'v', NULL)"
        )


def test_non_abstain_row_must_carry_no_abstain_reason(conn):
    seed_security(conn)
    seed_filing(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_event(conn, "e1", is_abstain=0, abstain_reason="should not be here")


def test_non_abstain_row_must_carry_a_confirmation_tier(conn):
    seed_security(conn)
    seed_filing(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            f"INSERT INTO news_events ({EVENT_COLUMNS}) VALUES "
            "('e1', 1, '0000000001-26-000001', 'x', 'doc.htm', 'x', 0, NULL, "
            "'binding_commercial_contract', NULL, 1, 5.0, NULL, NULL, NULL, NULL, NULL, "
            "'p', NULL, 'm', 'v', NULL)"
        )


def test_a_valid_abstain_row_is_accepted(conn):
    seed_security(conn)
    seed_filing(conn)
    insert_event(conn, "e1", is_abstain=1, abstain_reason="nothing to classify",
                 amount_explicit=0, amount_stated=None)
    conn.commit()  # must not raise


def test_a_valid_classified_row_is_accepted(conn):
    seed_security(conn)
    seed_filing(conn)
    insert_event(conn, "e1")
    conn.commit()  # must not raise


# ---------------------------------------------------------- never infer an amount


def test_amount_explicit_true_requires_a_stated_amount(conn):
    seed_security(conn)
    seed_filing(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_event(conn, "e1", amount_explicit=1, amount_stated=None)


def test_amount_explicit_false_forbids_a_stated_amount(conn):
    seed_security(conn)
    seed_filing(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_event(conn, "e1", amount_explicit=0, amount_stated=1.0)


# -------------------------------------------------------------------- append-only


def test_news_events_may_never_be_updated(conn):
    seed_security(conn)
    seed_filing(conn)
    insert_event(conn, "e1")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE news_events SET supporting_passage = 'rewritten' WHERE event_id = 'e1'")


def test_news_events_may_never_be_deleted(conn):
    seed_security(conn)
    seed_filing(conn)
    insert_event(conn, "e1")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM news_events WHERE event_id = 'e1'")


def test_a_correction_is_a_new_row_never_an_update(conn):
    seed_security(conn)
    seed_filing(conn)
    insert_event(conn, "e1", amount_stated=1_000_000.0)
    insert_event(conn, "e2", amount_stated=2_000_000.0, supersedes_event_id="e1")
    conn.commit()

    effective = conn.execute("SELECT event_id FROM effective_news_events").fetchall()
    assert [r["event_id"] for r in effective] == ["e2"]
    # The superseded row is untouched, not deleted -- just excluded from the view.
    original = conn.execute(
        "SELECT amount_stated FROM news_events WHERE event_id = 'e1'"
    ).fetchone()
    assert original["amount_stated"] == 1_000_000.0


def test_supersedes_event_id_cannot_self_reference(conn):
    seed_security(conn)
    seed_filing(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_event(conn, "e1", supersedes_event_id="e1")


# --------------------------------------------------- zero score influence: source scan
#
# Same technique as test_calibration.py's return-data scan: walk each target
# module's AST, collect every Name/Attribute identifier and every string
# constant that looks like SQL, and assert none of the four News
# tables/views appear. A module's own docstring/comments are exempt --
# stating "this never touches news_events" in prose must not fail the test.

SCORE_ADJACENT_FILES = (
    "pipeline/scoring/compute.py",
    "pipeline/scoring/percentiles.py",
    "pipeline/selection/compute.py",
    "pipeline/selection/rules.py",
    "pipeline/riskflags/compute.py",
    "pipeline/riskflags/detectors.py",
    "pipeline/riskflags/altman.py",
    "pipeline/riskflags/going_concern.py",
    "pipeline/execution/compute.py",
)


def _identifiers_and_sql_literals(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    signal: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            signal.add(node.id)
        if isinstance(node, ast.Attribute):
            signal.add(node.attr)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if "SELECT" in text or "FROM " in text or "JOIN" in text:
                signal.add(text)
    return signal


@pytest.mark.parametrize("relative_path", SCORE_ADJACENT_FILES)
def test_scoring_adjacent_module_never_references_news_tables(relative_path):
    path = REPO_ROOT / relative_path
    if not path.exists():
        pytest.skip(f"{relative_path} does not exist")
    signal = " ".join(_identifiers_and_sql_literals(path)).lower()
    for name in NEWS_IDENTIFIERS:
        assert name not in signal, f"{relative_path} must never reference {name!r}"


def test_migration_024_never_touches_experiments_or_frozen_config_lock():
    for suffix in ("up", "down"):
        path = REPO_ROOT / "migrations" / f"024_news_ledger.{suffix}.sql"
        text = path.read_text(encoding="utf-8").lower()
        # Strip SQL comments (-- to end of line) before scanning, so this
        # file's own explanatory prose about NOT touching those tables
        # cannot trip the assertion the way it legitimately mentions them.
        code = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
        assert "experiments" not in code
        assert "frozen_config_lock" not in code


# ------------------------------------------------------- v1 untouched, live check
#
# Baseline captured directly against the real project database (data/) right
# after migration 024 was applied and before pipeline/news/* wrote anything:
#   experiments: ('exp-d59006eb199b', 2, 'active', '2026-08-06T14:57:37Z')
#   frozen_config_lock (strategy_version=2): selection_rule_version=2,
#     config_hash='f6038e2321152e9bbca99dcce043fbfbb63ec41a9ad774d09b6ae97d44f30226',
#     locked_at='2026-08-06T09:08:06Z'
#   config_loader.py --digest, strategy_version=2: 61a5aa1b5a158309f6299a37909883ec6d163b23ff6065f2d78b9456767dc162
#
# Skipped if the real database is absent (a fresh clone / CI checkout).

REAL_DB = REPO_ROOT / "data" / "stockbot.db"


@pytest.mark.skipif(not REAL_DB.exists(), reason="real project database not present")
def test_v1_experiment_row_is_exactly_the_pre_news_baseline():
    conn = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT experiment_id, strategy_version, status, started_at FROM experiments "
        "WHERE experiment_id = 'exp-d59006eb199b'"
    ).fetchone()
    conn.close()
    assert row is not None, "v1's experiment row must still exist"
    assert (row["experiment_id"], row["strategy_version"], row["status"], row["started_at"]) == (
        "exp-d59006eb199b", 2, "active", "2026-08-06T14:57:37Z",
    )


@pytest.mark.skipif(not REAL_DB.exists(), reason="real project database not present")
def test_v1_frozen_config_lock_row_is_exactly_the_pre_news_baseline():
    conn = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT selection_rule_version, config_hash, locked_at FROM frozen_config_lock "
        "WHERE strategy_version = 2"
    ).fetchone()
    conn.close()
    assert row is not None
    assert (row["selection_rule_version"], row["config_hash"], row["locked_at"]) == (
        2, "f6038e2321152e9bbca99dcce043fbfbb63ec41a9ad774d09b6ae97d44f30226",
        "2026-08-06T09:08:06Z",
    )


@pytest.mark.skipif(not REAL_DB.exists(), reason="real project database not present")
def test_config_frozen_json_governed_digest_for_v1_is_unchanged():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "pipeline" / "config_loader.py"), "--digest"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    line = next(l for l in result.stdout.splitlines() if l.startswith("strategy_version=2"))
    assert line.strip() == (
        "strategy_version=2  digest=61a5aa1b5a158309f6299a37909883ec6d163b23ff6065f2d78b9456767dc162"
    )
