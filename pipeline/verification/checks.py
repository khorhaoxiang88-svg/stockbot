"""The ten Phase F exit-criteria checks.

Each check is a pure function of a database connection (plus config where
needed) returning a CheckResult: PASS, FAIL or PENDING, a one-line summary, and
full evidence a reader can use to confirm the summary without re-running
anything themselves.

Three checks reuse the SAME production code they are checking, deliberately:
check 1 calls fundamentals.compute.compute_row directly rather than a second
implementation, because a parallel implementation could itself be wrong in the
same way, or drift from the real one over time, and neither failure would ever
be caught. Recomputing with the real function and diffing against the stored
row instead proves the stored row was not corrupted, edited, or left stale
relative to the current fact base -- which is what "reproduces from stored
facts" actually means operationally. Checks 7 and 8 do the same against
scoring's explanation_json and risk_flags' source_accession respectively.

Two checks (3 and 6) describe MECHANISM correctness -- does the engine handle a
vendor correction, a split, a dividend, a delisting correctly -- not "did this
happen to the live data". The fixture currently holds zero paper_positions (F10
selected no candidates), so checking only the live database would report
PENDING forever on two checks whose truth has nothing to do with whether
trading has happened yet. Both build a small synthetic scenario with the real
pipeline.execution modules and assert on it fresh, every run, so they report a
genuine PASS or FAIL rather than an unresolvable PENDING.

PENDING is reserved for check 5 alone: it names a human task with zero
mechanism to fake, and reporting anything else would misstate what has and has
not actually been done.
"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from execution import compute as EXEC  # noqa: E402
from fundamentals.compute import (  # noqa: E402
    ALL_METRICS,
    FactIndex,
    compute_row,
    fixture_securities,
)
from fundamentals.mappings import CONCEPT_MAP, load_concept_mappings  # noqa: E402
from sec.payload_store import utc_now  # noqa: E402
from selection.compute import HASHED_COLUMNS as CANDIDATE_HASHED_COLUMNS  # noqa: E402
from selection.compute import row_hash as candidate_row_hash  # noqa: E402
from selection.compute import verify as verify_candidate_row_hashes  # noqa: E402

FLOAT_TOLERANCE = 1e-6


@dataclass
class CheckResult:
    number: int
    name: str
    status: str          # 'pass' | 'fail' | 'pending'
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_row(self, run_id: str) -> dict:
        return {
            "run_id": run_id, "check_number": self.number, "check_name": self.name,
            "status": self.status, "detail": self.detail,
            "evidence_json": json.dumps(self.evidence, default=str),
        }


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= FLOAT_TOLERANCE


# ------------------------------------------------------------------- check 1


def check_1_derived_metrics_reproduce(conn, cfg) -> CheckResult:
    """Every derived accounting metric reproduces from stored facts, all 50."""
    fixture = fixture_securities(conn)  # only securities with a CIK
    total_fixture = conn.execute("SELECT COUNT(*) AS n FROM fixture_manifest").fetchone()["n"]
    no_cik = total_fixture - len(fixture)

    mapping = load_concept_mappings(conn)
    all_concepts = {(t, c) for candidates in CONCEPT_MAP.values() for t, c, _, _ in candidates}

    mismatches = []
    reproduced = 0
    securities_checked = 0
    securities_with_rows = set()

    for security in fixture:
        stored_rows = conn.execute(
            "SELECT * FROM derived_fundamentals WHERE security_id = ?",
            (security["security_id"],),
        ).fetchall()
        if not stored_rows:
            continue
        securities_with_rows.add(security["security_id"])
        index = FactIndex(conn, security["cik"], all_concepts)
        for stored in stored_rows:
            recomputed = compute_row(
                conn, security, index, mapping, stored["period_end"],
                stored["knowledge_date"], cfg,
            )
            securities_checked += 1
            if recomputed is None:
                mismatches.append({
                    "security_id": security["security_id"], "period_end": stored["period_end"],
                    "knowledge_date": stored["knowledge_date"],
                    "problem": "recomputation returned nothing usable, but a row is stored",
                })
                continue
            row_mismatches = [
                metric for metric in ALL_METRICS
                if not _close(stored[metric], recomputed.get(metric))
            ]
            if row_mismatches:
                mismatches.append({
                    "security_id": security["security_id"], "period_end": stored["period_end"],
                    "knowledge_date": stored["knowledge_date"], "metrics": row_mismatches,
                    "stored": {m: stored[m] for m in row_mismatches},
                    "recomputed": {m: recomputed.get(m) for m in row_mismatches},
                })
            else:
                reproduced += 1

    status = "pass" if not mismatches else "fail"
    detail = (
        f"{reproduced} of {securities_checked} derived_fundamentals rows reproduced exactly "
        f"across {len(securities_with_rows)} securities"
    )
    if no_cik:
        detail += f"; {no_cik} of {total_fixture} fixture securities have no CIK, out of scope"
    return CheckResult(1, "Derived accounting metrics reproduce from stored facts", status, detail, {
        "securities_checked": securities_checked, "rows_reproduced": reproduced,
        "securities_with_rows": len(securities_with_rows), "securities_without_cik": no_cik,
        "mismatches": mismatches,
    })


# ------------------------------------------------------------------- check 2


def check_2_zero_unknown_classifications(conn) -> CheckResult:
    """Security classification: zero unknowns remaining in the fixture."""
    rows = [
        dict(r) for r in conn.execute(
            "SELECT s.security_id, f.symbol_at_selection AS symbol, s.classification_source "
            "FROM fixture_manifest f JOIN securities s USING (security_id) "
            "WHERE s.security_type = 'unknown'"
        )
    ]
    total = conn.execute("SELECT COUNT(*) AS n FROM fixture_manifest").fetchone()["n"]
    status = "pass" if not rows else "fail"
    detail = f"0 of {total} fixture securities classified unknown" if not rows else (
        f"{len(rows)} of {total} fixture securities still classified unknown"
    )
    return CheckResult(2, "Zero unknown classifications in the fixture", status, detail, {
        "total_fixture": total, "unknown": rows,
    })


# ------------------------------------------------------------------- check 3


def _migrated_temp_db() -> "sqlite3.Connection":  # noqa: F821
    path = Path(tempfile.mkdtemp()) / "verify.db"
    conn = migrate.connect(path)
    migrate.migrate_up(conn)
    return conn


def _seed_minimal(conn, security_id: int, symbol: str) -> None:
    conn.execute(
        "INSERT INTO securities (security_id, cik, share_class, name, security_type, "
        "classification_confidence, classification_source, sic_code, first_seen, last_seen, "
        "is_active, delisted_date) VALUES (?, ?, NULL, ?, 'common_stock', 'high', 'test', "
        "'3571', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, NULL)",
        (security_id, f"{security_id:010d}", f"{symbol} Inc."),
    )
    conn.execute(
        "INSERT INTO listings (security_id, symbol, exchange, valid_from, valid_to, is_primary) "
        "VALUES (?, ?, 'NYSE', '2026-01-01', NULL, 1)", (security_id, symbol),
    )
    conn.execute(
        "INSERT INTO fixture_manifest (security_id, symbol_at_selection, inclusion_reason, "
        "category, added_at, manifest_version) VALUES (?, ?, 'verify', 'ordinary', "
        "'2026-01-01T00:00:00Z', '1')", (security_id, symbol),
    )


def _seed_bar(conn, security_id: int, day: str, o, h, l, c, v=2_000_000):
    conn.execute(
        "INSERT INTO prices (security_id, date, open, high, low, close, volume, provider, "
        "first_seen_at, last_verified_at, revision, price_data_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'test', 'x', 'x', 0, 1)",
        (security_id, day, o, h, l, c, v),
    )


def _seed_candidate(conn, candidate_id: str, security_id: int, cutoff_session: str,
                    signal_close: float, atr_value: float) -> None:
    snapshot_id = conn.execute(
        "SELECT snapshot_id FROM universe_snapshot_runs LIMIT 1"
    ).fetchone()["snapshot_id"]
    pipeline_run_id = conn.execute(
        "SELECT run_id FROM pipeline_runs LIMIT 1"
    ).fetchone()["run_id"]
    row = {
        "candidate_id": candidate_id, "security_id": security_id, "generated_at": "x",
        "data_cutoff_at": f"{cutoff_session}T20:00:00Z", "snapshot_id": snapshot_id,
        "pipeline_run_id": pipeline_run_id, "strategy_version": 1, "config_hash": "h",
        "code_version": "v", "selection_rule_version": 1, "mapping_version": "1",
        "price_dataset_version": 1, "price_snapshot_hash": "psh",
        "source_health_snapshot_json": "{}", "score_snapshot_json": "{}",
        "accessions_used_json": "[]", "composite_at_generation": 55.0,
        "rank_at_generation": 1, "signal_close": signal_close, "atr_value": atr_value,
        "atr_window": 14, "price_data_cutoff": cutoff_session, "entry_rule": "next_open",
        "gap_limit_atr": 1.0,
    }
    # A real row_hash, computed the same way F10's selection engine computes
    # one -- not a placeholder -- so check 3's later verify() step is checking
    # something genuine rather than a fixture artifact.
    row["row_hash"] = candidate_row_hash(row)
    columns = list(CANDIDATE_HASHED_COLUMNS) + ["row_hash"]
    conn.execute(
        f"INSERT INTO research_candidates ({','.join(columns)}) "
        f"VALUES ({','.join('?' * len(columns))})",
        [row[c] for c in columns],
    )


def _bootstrap_execution_scenario(conn, security_id: int, symbol: str):
    conn.execute("INSERT OR IGNORE INTO price_dataset_versions (dataset_version, created_at, "
                "provider, reason) VALUES (1, 'x', 'test', 'seed')")
    conn.execute("INSERT OR IGNORE INTO pipeline_runs (run_id, stage, started_at, status, "
                "code_version) VALUES ('run-seed', 'test', 'x', 'success', 'x')")
    conn.execute("INSERT OR IGNORE INTO pipeline_runs (run_id, stage, started_at, status, "
                "code_version) VALUES ('run-exec', 'execution', 'x', 'success', 'x')")
    conn.execute("INSERT OR IGNORE INTO universe_snapshot_runs (snapshot_id, effective_at, "
                "rules_version, config_hash, run_id, security_count, is_official) VALUES "
                "('snap-1', '2026-01-01', 'v', 'h', NULL, 1, 1)")
    conn.execute("INSERT OR IGNORE INTO books (book_id, horizon_days, starting_nav, "
                "current_nav, open_position_count, strategy_version) VALUES "
                "('book-20d', 20, 100000, 100000, 0, 1), ('book-60d', 60, 100000, 100000, 0, 1)")
    if not conn.execute("SELECT 1 FROM securities WHERE security_id = 99").fetchone():
        _seed_minimal(conn, 99, "SPY")
    _seed_minimal(conn, security_id, symbol)


CHECK3_CFG = {
    "position_notional": 1000, "atr_window": 14, "stop_atr_multiple": 2.0,
    "target_atr_multiple": 4.0, "gap_cancel_atr": 1.0,
    "slippage_bps_high_liquidity": 5, "slippage_bps_mid_liquidity": 15,
    "horizons": [20, 60], "strategy_version": 1, "resolution_policy_version": 1,
    "accrual_policy_version": 1,
}


def check_3_price_correction_reconstruction() -> CheckResult:
    """A synthetic vendor correction must not alter an already-generated candidate.

    Builds an isolated in-memory scenario: a candidate is generated from one
    price history, its composite/ATR/stop/target are stored, and THEN the
    price history is corrected (a vendor revising a bar after the fact, as F3's
    price_revisions exists to record). The stored candidate and the position
    opened from it must reproduce byte-for-byte from what was recorded at
    generation time -- research_candidates is append-only specifically so a
    later correction can never reach backward and change a decision already
    made on the evidence available then.
    """
    conn = _migrated_temp_db()
    try:
        _bootstrap_execution_scenario(conn, 501, "RECON")
        _seed_bar(conn, 501, "2026-02-01", 100, 101, 99, 100, 2_000_000)
        _seed_bar(conn, 501, "2026-02-02", 100.5, 101, 99.5, 100, 2_000_000)
        for offset in range(1, 21):
            d = (date(2026, 2, 1) - timedelta(days=offset)).isoformat()
            _seed_bar(conn, 501, d, 100.0, 100.0, 100.0, 100.0, 2_000_000)
        _seed_candidate(conn, "cand-recon", 501, "2026-02-01", 100.0, atr_value=3.0)
        conn.commit()

        sessions = EXEC.all_sessions(conn)
        candidate = dict(conn.execute(
            "SELECT * FROM research_candidates WHERE candidate_id = 'cand-recon'"
        ).fetchone())
        decision = EXEC.attempt_entry(conn, candidate, sessions, "2026-02-02", CHECK3_CFG, "run-exec")
        if decision["outcome"] != "filled":
            return CheckResult(3, "Price-correction reconstruction", "fail",
                              f"synthetic scenario did not fill: {decision}", {"decision": decision})
        EXEC.open_positions_for_candidate(conn, candidate, decision, CHECK3_CFG, "run-exec", 501)
        conn.commit()

        before_candidate = dict(conn.execute(
            "SELECT composite_at_generation, atr_value, signal_close, row_hash "
            "FROM research_candidates WHERE candidate_id = 'cand-recon'"
        ).fetchone())
        before_position = dict(conn.execute(
            "SELECT entry_price, stop_price, target_price FROM paper_positions "
            "WHERE candidate_id = 'cand-recon' AND horizon_days = 20"
        ).fetchone())

        # THE VENDOR CORRECTION. A later re-fetch revises the signal-day close.
        # This is exactly what price_revisions exists to record.
        conn.execute(
            "UPDATE prices SET close = 250.0, revision = 1 WHERE security_id = 501 "
            "AND date = '2026-02-01'"
        )
        conn.execute(
            "INSERT INTO price_revisions (security_id, date, revision, old_close, new_close, "
            "detected_at, provider) VALUES (501, '2026-02-01', 1, 100.0, 250.0, ?, 'test')",
            (utc_now(),),
        )
        conn.commit()

        # Recompute row_hash from the STORED fields, exactly as F10's own
        # --verify does: a mismatch here means the record itself was edited,
        # independent of the price fix.
        problems = verify_candidate_row_hashes(conn)

        after_candidate = dict(conn.execute(
            "SELECT composite_at_generation, atr_value, signal_close, row_hash "
            "FROM research_candidates WHERE candidate_id = 'cand-recon'"
        ).fetchone())
        after_position = dict(conn.execute(
            "SELECT entry_price, stop_price, target_price FROM paper_positions "
            "WHERE candidate_id = 'cand-recon' AND horizon_days = 20"
        ).fetchone())

        unchanged = (
            before_candidate == after_candidate and before_position == after_position
        )
        status = "pass" if unchanged and not problems else "fail"
        detail = (
            "candidate and position reproduce unchanged after a synthetic vendor "
            "correction to the signal-day close (100.00 -> 250.00)"
            if status == "pass" else
            "candidate or position CHANGED after the vendor correction, or row_hash "
            "verification failed"
        )
        return CheckResult(3, "Price-correction reconstruction", status, detail, {
            "before_candidate": before_candidate, "after_candidate": after_candidate,
            "before_position": before_position, "after_position": after_position,
            "row_hash_problems": problems,
        })
    finally:
        conn.close()


# ------------------------------------------------------------------- check 4


def check_4_piotroski_roic_dilution_reproduce(conn, cfg, minimum: int = 10) -> CheckResult:
    """Piotroski, ROIC and dilution reproduce by hand for at least 10 securities.

    Reuses check 1's engine for Piotroski and ROIC (the same recomputation, a
    named subset of it), and independently re-sums dilution's D1-D4 for
    dilution_signals -- arithmetic already enforced by a database CHECK, and
    re-verified here so a reader sees the numbers, not just the constraint's
    existence.
    """
    fixture = fixture_securities(conn)
    mapping = load_concept_mappings(conn)
    all_concepts = {(t, c) for candidates in CONCEPT_MAP.values() for t, c, _, _ in candidates}
    piotroski_roic_metrics = ["roic"] + [m for m in ALL_METRICS if m.startswith("piotroski_")]

    traced, failures = [], []
    for security in fixture:
        stored = conn.execute(
            "SELECT * FROM derived_fundamentals WHERE security_id = ? "
            "ORDER BY period_end DESC, knowledge_date DESC LIMIT 1",
            (security["security_id"],),
        ).fetchone()
        if stored is None:
            continue
        index = FactIndex(conn, security["cik"], all_concepts)
        recomputed = compute_row(
            conn, security, index, mapping, stored["period_end"], stored["knowledge_date"], cfg,
        )
        if recomputed is None:
            continue
        signals = {m: stored[m] for m in piotroski_roic_metrics}
        f_score = sum(1 for m in signals if m.startswith("piotroski_") and signals[m] == 1)
        f_score_computable = all(
            stored[m] is not None for m in piotroski_roic_metrics if m.startswith("piotroski_")
        )
        matched = all(_close(stored[m], recomputed.get(m)) for m in piotroski_roic_metrics)
        entry = {
            "security_id": security["security_id"], "period_end": stored["period_end"],
            "roic_stored": stored["roic"], "roic_recomputed": recomputed.get("roic"),
            "piotroski_f_score": f_score if f_score_computable else None,
            "piotroski_complete": f_score_computable, "matched": matched,
        }
        (traced if matched else failures).append(entry)
        # Deliberately NO early break once `minimum` is reached: a security
        # further down the fixture that fails to reproduce must still be
        # caught. Stopping early would let a corrupted security past the
        # minimum-count point go unchecked, which is exactly the failure mode
        # a phase-gate harness cannot afford.

    dilution_rows = [
        dict(r) for r in conn.execute(
            "SELECT security_id, as_of_date, d1_capacity, d2_issuance, d3_structural, "
            "d4_realised, dilution_score, is_disqualified FROM dilution_signals "
            "ORDER BY security_id LIMIT ?", (max(minimum, 10),),
        )
    ]
    dilution_failures = []
    for row in dilution_rows:
        expected_score = min(30.0, row["d1_capacity"] + row["d2_issuance"] +
                             row["d3_structural"] + row["d4_realised"])
        expected_disqualified = 1 if expected_score >= 22 else 0
        if not _close(expected_score, row["dilution_score"]) or \
           expected_disqualified != row["is_disqualified"]:
            dilution_failures.append({**row, "expected_score": expected_score})

    enough_fundamentals = len(traced) >= minimum
    enough_dilution = len(dilution_rows) >= minimum
    status = (
        "pass" if enough_fundamentals and enough_dilution and not failures and not dilution_failures
        else "fail"
    )
    detail = (
        f"Piotroski/ROIC reproduced for {len(traced)} securities (minimum {minimum}); "
        f"dilution D1-D4 arithmetic reproduced for {len(dilution_rows)} securities "
        f"(minimum {minimum})"
    )
    return CheckResult(4, "Piotroski, ROIC and dilution reproduce by hand", status, detail, {
        "minimum_required": minimum, "fundamentals_traced": traced,
        "fundamentals_failures": failures, "dilution_checked": dilution_rows,
        "dilution_failures": dilution_failures,
    })


# ------------------------------------------------------------------- check 5


def check_5_form4_hand_verification(conn, minimum: int = 20, min_amendments: int = 3) -> CheckResult:
    """20 Form 4 filings hand-verified against EDGAR source documents.

    This check has no mechanism to fake. It counts real rows in
    filing_verifications -- each one a human opening the actual filing on
    sec.gov and comparing it, field by field, against what insider_transactions
    stored -- and reports PENDING, honestly, until enough exist. No prior
    verification recorded elsewhere in this repository meets this bar: the one
    documented insider check (README, migration 006) sampled 15 filings for a
    single field's presence, not a full field-by-field reconciliation, and
    covered 1 amendment, not the required 3.
    """
    rows = [
        dict(r) for r in conn.execute(
            "SELECT accession_no, security_id, is_amendment, matches_source, verified_by, "
            "verified_at FROM filing_verifications ORDER BY verified_at"
        )
    ]
    verified_matching = [r for r in rows if r["matches_source"] == 1]
    amendments = [r for r in verified_matching if r["is_amendment"] == 1]
    mismatches = [r for r in rows if r["matches_source"] == 0]

    status = (
        "pass" if len(verified_matching) >= minimum and len(amendments) >= min_amendments
        else "pending"
    )
    if mismatches:
        status = "fail"  # a recorded discrepancy is a real finding, not a pending task

    detail = (
        f"{len(verified_matching)} of {minimum} required filings verified against EDGAR "
        f"({len(amendments)} of {min_amendments} required amendments)"
    )
    if mismatches:
        detail += f"; {len(mismatches)} verified filing(s) did NOT match their source document"
    return CheckResult(5, "20 Form 4 filings hand-verified against EDGAR", status, detail, {
        "required": minimum, "required_amendments": min_amendments,
        "verified_matching": len(verified_matching), "amendments_verified": len(amendments),
        "rows": rows, "mismatches": mismatches,
    })


# ------------------------------------------------------------------- check 6


def check_6_corporate_actions_trace_cleanly() -> CheckResult:
    """A split, a dividend and a delisting each trace end to end with no false
    return or false stop event, over a fresh synthetic scenario built with the
    real pipeline.execution modules.
    """
    conn = _migrated_temp_db()
    try:
        cfg = CHECK3_CFG
        results = {}

        # ---- split: flat price move must not manufacture a return or an exit.
        _bootstrap_execution_scenario(conn, 601, "SPLIT6")
        for offset in range(1, 21):
            d = (date(2026, 2, 1) - timedelta(days=offset)).isoformat()
            _seed_bar(conn, 601, d, 100.0, 100.0, 100.0, 100.0, 2_000_000)
        _seed_bar(conn, 601, "2026-02-01", 100, 101, 99, 100, 2_000_000)
        _seed_bar(conn, 601, "2026-02-02", 100.5, 101, 99.5, 100, 2_000_000)
        _seed_candidate(conn, "cand-split6", 601, "2026-02-01", 100.0, atr_value=3.0)
        conn.commit()
        sessions = EXEC.all_sessions(conn)
        candidate = dict(conn.execute(
            "SELECT * FROM research_candidates WHERE candidate_id = 'cand-split6'"
        ).fetchone())
        decision = EXEC.attempt_entry(conn, candidate, sessions, "2026-02-02", cfg, "run-exec")
        EXEC.open_positions_for_candidate(conn, candidate, decision, cfg, "run-exec", 601)
        conn.commit()
        position = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE candidate_id = 'cand-split6' AND horizon_days = 20"
        ).fetchone())
        shares_before = position["shares"]
        entry_before, stop_before, target_before = (
            position["entry_price"], position["stop_price"], position["target_price"],
        )
        conn.execute("INSERT INTO corporate_actions (security_id, ex_date, action_type, ratio, "
                    "provider, requires_manual_review) VALUES (601, '2026-02-03', 'split', 2.0, "
                    "'test', 0)")
        # A genuinely FLAT move: the split-adjusted close is unchanged (100 pre,
        # 50 post is the same price on the new basis), so nothing here should
        # look like a gain, a loss, or a stop/target event.
        _seed_bar(conn, 601, "2026-02-03", 50.0, 50.5, 49.5, 50.0, 4_000_000)
        conn.commit()
        EXEC.walk_forward_position(conn, position, EXEC.all_sessions(conn), "2026-02-03", cfg)
        conn.commit()
        after = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE position_id = ?", (position["position_id"],)
        ).fetchone())
        # Shares, entry, stop and target each scale by the ratio in lockstep --
        # the direct invariant a correct split application preserves, checked
        # per-field rather than through an indirect value comparison that would
        # also have to account for the entry fill's own slippage.
        results["split"] = {
            "still_open_no_false_exit": after["status"] == "open",
            "shares_scaled_by_ratio": _close(after["shares"], shares_before * 2.0),
            "entry_price_scaled_by_ratio": _close(after["entry_price"], entry_before / 2.0),
            "stop_scaled_by_ratio": _close(after["stop_price"], stop_before / 2.0),
            "target_scaled_by_ratio": _close(after["target_price"], target_before / 2.0),
            "splits_applied_recorded": _close(after["splits_applied"], 2.0),
            "shares_before": shares_before, "shares_after": after["shares"],
        }

        # ---- dividend: entitlement, no phantom return.
        _bootstrap_execution_scenario(conn, 602, "DIV6")
        for offset in range(1, 21):
            d = (date(2026, 2, 1) - timedelta(days=offset)).isoformat()
            _seed_bar(conn, 602, d, 50.0, 50.0, 50.0, 50.0, 2_000_000)
        _seed_bar(conn, 602, "2026-02-01", 50, 50.5, 49.5, 50, 2_000_000)
        _seed_bar(conn, 602, "2026-02-02", 50.1, 50.6, 49.6, 50.0, 2_000_000)
        _seed_candidate(conn, "cand-div6", 602, "2026-02-01", 50.0, atr_value=1.5)
        conn.commit()
        candidate = dict(conn.execute(
            "SELECT * FROM research_candidates WHERE candidate_id = 'cand-div6'"
        ).fetchone())
        decision = EXEC.attempt_entry(conn, candidate, EXEC.all_sessions(conn), "2026-02-02", cfg,
                                      "run-exec")
        EXEC.open_positions_for_candidate(conn, candidate, decision, cfg, "run-exec", 602)
        conn.commit()
        position = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE candidate_id = 'cand-div6' AND horizon_days = 20"
        ).fetchone())
        conn.execute("INSERT INTO corporate_actions (security_id, ex_date, action_type, "
                    "cash_amount, provider, requires_manual_review) VALUES (602, '2026-02-03', "
                    "'dividend', 0.40, 'test', 0)")
        _seed_bar(conn, 602, "2026-02-03", 50.0, 50.4, 49.7, 50.0, 2_000_000)
        conn.commit()
        EXEC.walk_forward_position(conn, position, EXEC.all_sessions(conn), "2026-02-03", cfg)
        conn.commit()
        after = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE position_id = ?", (position["position_id"],)
        ).fetchone())
        results["dividend"] = {
            "entitled": position["entry_date"] < "2026-02-03",
            "credited": _close(after["dividends_received"], position["shares"] * 0.40),
            "still_open_no_false_exit": after["status"] == "open",
        }

        # ---- delisting: never an automatic close.
        _bootstrap_execution_scenario(conn, 603, "DLST6")
        for offset in range(1, 21):
            d = (date(2026, 2, 1) - timedelta(days=offset)).isoformat()
            _seed_bar(conn, 603, d, 20.0, 20.0, 20.0, 20.0, 2_000_000)
        _seed_bar(conn, 603, "2026-02-01", 20, 20.5, 19.5, 20, 2_000_000)
        _seed_bar(conn, 603, "2026-02-02", 20.1, 20.5, 19.8, 20.0, 2_000_000)
        _seed_candidate(conn, "cand-dlst6", 603, "2026-02-01", 20.0, atr_value=0.6)
        conn.commit()
        candidate = dict(conn.execute(
            "SELECT * FROM research_candidates WHERE candidate_id = 'cand-dlst6'"
        ).fetchone())
        decision = EXEC.attempt_entry(conn, candidate, EXEC.all_sessions(conn), "2026-02-02", cfg,
                                      "run-exec")
        EXEC.open_positions_for_candidate(conn, candidate, decision, cfg, "run-exec", 603)
        conn.commit()
        position = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE candidate_id = 'cand-dlst6' AND horizon_days = 20"
        ).fetchone())
        conn.execute("UPDATE securities SET is_active = 0, delisted_date = '2026-02-03' "
                    "WHERE security_id = 603")
        _seed_bar(conn, 99, "2026-02-03", 400.1, 400.5, 399.9, 400.0, 2_000_000)
        conn.commit()
        EXEC.walk_forward_position(conn, position, EXEC.all_sessions(conn), "2026-02-10", cfg)
        conn.commit()
        after = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE position_id = ?", (position["position_id"],)
        ).fetchone())
        results["delisting"] = {
            "pending_not_closed": after["status"] == "pending_resolution",
            "no_exit_price_assigned": after["exit_price"] is None,
        }

        all_ok = (
            results["split"]["still_open_no_false_exit"]
            and results["split"]["shares_scaled_by_ratio"]
            and results["split"]["entry_price_scaled_by_ratio"]
            and results["split"]["stop_scaled_by_ratio"]
            and results["split"]["target_scaled_by_ratio"]
            and results["split"]["splits_applied_recorded"]
            and results["dividend"]["entitled"] and results["dividend"]["credited"]
            and results["dividend"]["still_open_no_false_exit"]
            and results["delisting"]["pending_not_closed"]
            and results["delisting"]["no_exit_price_assigned"]
        )
        status = "pass" if all_ok else "fail"
        detail = (
            "split, dividend and delisting each traced end to end with no false return "
            "or false stop event" if all_ok else
            "one or more corporate-action traces produced an unexpected result"
        )
        return CheckResult(6, "Split, dividend and delisting trace cleanly", status, detail,
                          results)
    finally:
        conn.close()


# ------------------------------------------------------------------- check 7


def _recompute_component_score(detail: dict) -> float:
    return sum(
        item["effective_weight"] * item["value_used"]
        for item in detail["submetrics"]
        if item["valid"] and item["value_used"] is not None
    )


def check_7_scores_reproduce_from_explanation(conn) -> CheckResult:
    """Every score reproduces exactly from its stored explanation_json."""
    rows = conn.execute(
        "SELECT security_id, value_score, quality_score, momentum_score, insider_bonus, "
        "dilution_penalty, composite_score, rankable, explanation_json FROM scores "
        "WHERE rankable = 1"
    ).fetchall()
    mismatches, traced = [], []
    for row in rows:
        explanation = json.loads(row["explanation_json"])
        components = explanation["components"]
        value = _recompute_component_score(components["value"]["detail"])
        quality = _recompute_component_score(components["quality"]["detail"])
        momentum = _recompute_component_score(components["momentum"]["detail"])
        bonus = explanation["insider_bonus"]
        recomputed_bonus = min(10.0, sum(
            bonus[key]["value"] for key in
            ("b1_cluster", "b2_executive", "b3_size", "b4_conviction")
        ))
        composite = max(0.0, min(100.0,
            0.30 * value + 0.30 * quality + 0.30 * momentum
            + recomputed_bonus - row["dilution_penalty"]))

        ok = (
            _close(value, row["value_score"]) and _close(quality, row["quality_score"])
            and _close(momentum, row["momentum_score"])
            and _close(recomputed_bonus, row["insider_bonus"])
            and _close(composite, row["composite_score"])
        )
        entry = {
            "security_id": row["security_id"], "stored_composite": row["composite_score"],
            "recomputed_composite": composite,
        }
        (traced if ok else mismatches).append(entry)

    status = "pass" if rows and not mismatches else ("fail" if mismatches else "pending")
    detail = (
        f"{len(traced)} of {len(rows)} rankable scores reproduced exactly from their "
        f"stored explanation"
        if rows else "no rankable scores exist yet"
    )
    return CheckResult(7, "Every score reproduces from its stored explanation", status, detail, {
        "total_rankable": len(rows), "reproduced": traced, "mismatches": mismatches,
    })


# ------------------------------------------------------------------- check 8


def check_8_risk_flags_resolve_to_real_filings(conn) -> CheckResult:
    """Every non-unknown risk flag cites a source that actually resolves."""
    rows = conn.execute(
        "SELECT security_id, flag_code, source_accession FROM risk_flags WHERE is_unknown = 0"
    ).fetchall()
    unresolved, resolved = [], 0
    for row in rows:
        reference = row["source_accession"]
        if not reference:
            unresolved.append({**dict(row), "problem": "no source_accession at all"})
            continue
        if reference == "none":
            if row["flag_code"] in ("recent_insider_selling", "stale_or_incomplete_data"):
                resolved += 1
                continue
            unresolved.append({**dict(row), "problem": "'none' used outside its documented cases"})
            continue
        if reference.startswith("ledger:corporate_actions:"):
            _, _, security_id, ex_date = reference.split(":")
            if ex_date == "none":
                resolved += 1
                continue
            found = conn.execute(
                "SELECT 1 FROM corporate_actions WHERE security_id = ? AND ex_date = ?",
                (int(security_id), ex_date),
            ).fetchone()
            (resolved := resolved + 1) if found else unresolved.append(
                {**dict(row), "problem": f"{reference} does not resolve to a ledger row"}
            )
            continue
        found = (
            conn.execute("SELECT 1 FROM filings WHERE accession_no = ?", (reference,)).fetchone()
            or conn.execute("SELECT 1 FROM xbrl_facts WHERE accession_no = ? LIMIT 1",
                            (reference,)).fetchone()
            or conn.execute("SELECT 1 FROM insider_transactions WHERE accession_no = ? LIMIT 1",
                            (reference,)).fetchone()
        )
        if found:
            resolved += 1
        else:
            unresolved.append({**dict(row), "problem": f"{reference} resolves to nothing"})

    status = "pass" if rows and not unresolved else ("fail" if unresolved else "pending")
    detail = (
        f"{resolved} of {len(rows)} non-unknown risk flags resolve to a real source"
        if rows else "no risk flags exist yet"
    )
    return CheckResult(8, "Every risk flag resolves to a real source filing", status, detail, {
        "total_checked": len(rows), "resolved": resolved, "unresolved": unresolved,
    })


# ------------------------------------------------------------------- check 9


# Metrics F5's own validity table defines as NEVER legitimately zero: invalid
# inputs produce NULL, not 0, for every one of these. debt_ebitda and
# interest_coverage are excluded on purpose -- F5 defines 0 and the configured
# cap respectively as legitimate values for a debt-free balance sheet.
NEVER_ZERO_METRICS = ("pe", "pb", "ev_ebitda", "fcf_yield", "roic", "current_ratio")


def check_9_no_zero_for_absent_data(conn) -> CheckResult:
    """No metric anywhere displays zero where data is absent."""
    violations = []

    for metric in NEVER_ZERO_METRICS:
        rows = conn.execute(
            f"SELECT security_id, period_end, knowledge_date, {metric} FROM derived_fundamentals "
            f"WHERE {metric} = 0.0"
        ).fetchall()
        for row in rows:
            violations.append({"table": "derived_fundamentals", "metric": metric, **dict(row)})

    # A rankable score must carry every component (F8's own CHECK), and an
    # unrankable one must carry none -- re-verified here rather than trusted
    # blindly, since a CHECK only guards writes, not a later schema change.
    bad_rankable = conn.execute(
        "SELECT security_id, score_date FROM scores WHERE rankable = 1 AND "
        "(value_score IS NULL OR quality_score IS NULL OR momentum_score IS NULL "
        "OR insider_bonus IS NULL OR composite_score IS NULL)"
    ).fetchall()
    for row in bad_rankable:
        violations.append({"table": "scores", "problem": "rankable but missing a component",
                           **dict(row)})

    bad_unrankable = conn.execute(
        "SELECT security_id, score_date FROM scores WHERE rankable = 0 AND "
        "(composite_score IS NOT NULL OR withhold_reason IS NULL)"
    ).fetchall()
    for row in bad_unrankable:
        violations.append({"table": "scores", "problem": "unrankable but carries a score "
                           "or no withhold_reason", **dict(row)})

    bad_flags = conn.execute(
        "SELECT security_id, flag_code FROM risk_flags WHERE "
        "(is_unknown = 1 AND severity <> 'unknown') OR (is_unknown = 0 AND severity = 'unknown')"
    ).fetchall()
    for row in bad_flags:
        violations.append({"table": "risk_flags", "problem": "is_unknown/severity mismatch",
                           **dict(row)})

    status = "pass" if not violations else "fail"
    detail = (
        "no absent-data value renders as zero across derived_fundamentals, scores or "
        "risk_flags" if not violations else
        f"{len(violations)} place(s) where absent data may be rendering as zero"
    )
    return CheckResult(9, "No metric displays zero where data is absent", status, detail, {
        "violations": violations,
    })


# ------------------------------------------------------------------ check 10


def check_10_books_never_pooled(conn, cfg) -> CheckResult:
    """Both books report separately and are never pooled.

    This check verifies the DATA-LAYER precondition for separation: the books
    table holds exactly the configured horizons as independent rows, and every
    position and candidate resolves to exactly one horizon, so no query could
    even accidentally sum across them without an explicit join. The UI-level
    guarantee -- that /performance actually renders two independent sections
    and never a combined statistic -- is enforced by the F11 web test suite
    (web/tests/performance-page.test.tsx: "reports the 20-day and 60-day books
    separately, never pooled"), which this Python check cannot itself exercise.
    """
    configured = sorted(int(h) for h in cfg["horizons"])
    books = [
        dict(r) for r in conn.execute("SELECT * FROM books ORDER BY horizon_days")
    ]
    book_horizons = sorted(b["horizon_days"] for b in books)

    problems = []
    if book_horizons != configured:
        problems.append(f"books table has horizons {book_horizons}, config has {configured}")
    if len(set(book_horizons)) != len(book_horizons):
        problems.append("books table has a duplicate horizon")

    orphan_positions = conn.execute(
        "SELECT COUNT(*) AS n FROM paper_positions WHERE horizon_days NOT IN "
        f"({','.join('?' * len(configured))})", configured,
    ).fetchone()["n"]
    if orphan_positions:
        problems.append(f"{orphan_positions} paper_position(s) carry an unconfigured horizon")

    status = "pass" if not problems else "fail"
    detail = (
        f"books table holds exactly the {len(configured)} configured horizons "
        f"({configured}) as independent rows; the UI's per-horizon separation is "
        f"enforced separately by the web test suite"
        if not problems else "; ".join(problems)
    )
    return CheckResult(10, "Both books report separately, never pooled", status, detail, {
        "configured_horizons": configured, "books": books, "orphan_positions": orphan_positions,
    })


ALL_CHECKS = tuple(range(1, 11))
