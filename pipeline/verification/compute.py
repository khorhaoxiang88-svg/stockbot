"""Run all ten Phase F exit-criteria checks and record the result.

"Phase S may not begin until every check passes" means all ten report PASS,
specifically -- not "mostly pass" and not "the ones with live data pass". A
PENDING check blocks Phase S exactly as a FAIL does; the distinction exists
only to say WHY it is blocked (no mechanism has run yet, versus a mechanism
ran and found a problem), never to treat one as acceptable and the other not.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from sec.payload_store import utc_now  # noqa: E402
from verification import checks as C  # noqa: E402

CODE_VERSION = "verification-harness/v1"


def run_all(conn, cfg) -> list[C.CheckResult]:
    return [
        C.check_1_derived_metrics_reproduce(conn, cfg),
        C.check_2_zero_unknown_classifications(conn),
        C.check_3_price_correction_reconstruction(),
        C.check_4_piotroski_roic_dilution_reproduce(conn, cfg),
        C.check_5_form4_hand_verification(conn),
        C.check_6_corporate_actions_trace_cleanly(),
        C.check_7_scores_reproduce_from_explanation(conn),
        C.check_8_risk_flags_resolve_to_real_filings(conn),
        C.check_9_no_zero_for_absent_data(conn),
        C.check_10_books_never_pooled(conn, cfg),
    ]


def format_report(results: list[C.CheckResult]) -> str:
    lines = ["", "PHASE F EXIT-CRITERIA VERIFICATION", ""]
    symbol = {"pass": "PASS", "fail": "FAIL", "pending": "PENDING"}
    for result in results:
        lines.append(f"  [{symbol[result.status]:>7}] {result.number:>2}. {result.name}")
        lines.append(f"           {result.detail}")
    passed = sum(1 for r in results if r.status == "pass")
    lines += [
        "",
        f"{passed} of {len(results)} checks PASS.",
        (
            "Phase S may begin." if passed == len(results)
            else "Phase S may NOT begin until every check passes."
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase F exit-criteria harness")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    conn = migrate.connect(Path(args.db))
    run_id = f"verification-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'verification', ?, 'running', ?)", (run_id, utc_now(), CODE_VERSION),
        )
        results = run_all(conn, cfg)
        for result in results:
            row = result.as_row(run_id)
            conn.execute(
                "INSERT OR REPLACE INTO verification_results (run_id, check_number, check_name, "
                "status, detail, evidence_json) VALUES (?, ?, ?, ?, ?, ?)",
                (row["run_id"], row["check_number"], row["check_name"], row["status"],
                 row["detail"], row["evidence_json"]),
            )
        passed = sum(1 for r in results if r.status == "pass")
        conn.execute(
            "UPDATE pipeline_runs SET status=?, finished_at=?, records_written=? WHERE run_id=?",
            ("success" if passed == len(results) else "partial", utc_now(), passed, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print(format_report(results))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
