"""Weekly research-candidate selection, over the Phase F fixture (default) or a
named universe pool (--pool, repeatable; NOT official, matching every other
Phase S pool-scoped output).

Fully automatic. There is no flag, argument or code path that lets a human add,
remove or reorder a candidate, and the append-only triggers plus row_hash make a
manual edit to the stored result detectable rather than merely discouraged.

Order of operations:

  1. Resolve the SELECTION WEEK from the price calendar and refuse to run for a
     week that is not provably over.
  2. Establish the evidence cutoff: that week's regular close, in UTC.
  3. Check PIPELINE freshness. A failure here blocks every candidate, because
     selecting on numbers we cannot vouch for is worse than selecting nothing.
  4. Load the score and risk state AS OF THE CUTOFF. Not the newest available --
     the newest available is next week's information.
  5. Apply the rule, logging every suppression.
  6. Write candidates with full provenance and a row hash over every field.

Step 4 is the one that bites. F8 scores and F9 flags are stamped with their own
as-of dates, and the newest of those is usually AFTER the selection cutoff. Using
them would be a lookahead of exactly the kind this phase exists to prevent, so
the run refuses rather than reaching for the nearest available row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import date
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from prices.adjust import adjusted_series  # noqa: E402
from scoring.compute import config_hash, universe_rows  # noqa: E402
from sec.payload_store import utc_now  # noqa: E402
from selection import trading_calendar as CAL  # noqa: E402
from selection import freshness as FR  # noqa: E402
from selection import rules as R  # noqa: E402

CODE_VERSION = "selection-rule-1.1/v1"
ENTRY_RULE = "next_regular_session_open, subject to the gap filter"
BOOK_IDS = {20: "book-20d", 60: "book-60d"}

# Every column of research_candidates except row_hash, in a fixed order. The
# hash is over this list, so adding a column without adding it here would leave
# the new field unprotected -- a test asserts the two stay in step.
HASHED_COLUMNS = (
    "candidate_id", "security_id", "generated_at", "data_cutoff_at", "snapshot_id",
    "pipeline_run_id", "strategy_version", "config_hash", "code_version",
    "selection_rule_version", "mapping_version", "price_dataset_version",
    "price_snapshot_hash", "source_health_snapshot_json", "score_snapshot_json",
    "accessions_used_json", "composite_at_generation", "rank_at_generation",
    "signal_close", "atr_value", "atr_window", "price_data_cutoff", "entry_rule",
    "gap_limit_atr",
)


def canonical(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(value)
    return str(value)


def row_hash(row: dict) -> str:
    """sha256 over every field of a candidate. See HASHED_COLUMNS."""
    payload = "\n".join(f"{key}={canonical(row.get(key))}" for key in HASHED_COLUMNS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_identity(security_id: int, cutoff: str, strategy_version, rule_version) -> str:
    """Deterministic id, so re-running a week cannot duplicate its candidates.

    A random id would let the same week be selected twice into two sets of rows,
    and append-only means neither could be removed. Deriving the id from
    (security, cutoff, versions) makes a re-run a no-op instead, which is also
    what makes "run it twice, get the same output" true at the storage layer and
    not merely in the report.
    """
    seed = f"{security_id}|{cutoff}|{strategy_version}|{rule_version}"
    return "cand-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def atr(bars, window: int) -> float | None:
    """Average true range over the split-adjusted series."""
    usable = [b for b in bars if b.high is not None and b.low is not None and b.close is not None]
    if len(usable) < window + 1:
        return None
    ranges = []
    for previous, current in zip(usable[-window - 1:-1], usable[-window:]):
        ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(ranges) / len(ranges)


# ------------------------------------------------------------------ gathering


def load_rows(
    conn, cutoff_session: str, cutoff_utc: str, session_dates: list[str], cfg,
    pool_versions: list[str] | None = None,
) -> tuple[list[R.Row], list[dict], dict, list[dict]]:
    """Every security in the named population (the Phase F fixture by default,
    or the given universe pool version(s)) as the rule sees it, plus
    evidence-cutoff faults.

    A security with no dilution_signals row, or no risk_flags row at all as of
    this cutoff, has never actually been screened by dilution/compute.py or
    riskflags/compute.py. Defaulting it to dilution_score=0.0 and
    high_going_concern=False would read as "checked, clean", which is false --
    exactly the zero-fill rule 5 forbids everywhere else in this system. Such a
    security is excluded from `rows` and returned separately in the fourth
    element, so the caller logs it as suppressed (unknown) rather than
    silently treating it as eligible.
    """
    scores = {
        int(r["security_id"]): dict(r)
        for r in conn.execute(
            'SELECT security_id, composite_score, "rank", quality_score, rankable, '
            "cohort_id, explanation_json, config_hash, mapping_version, "
            "price_dataset_version, price_snapshot_hash, strategy_version "
            "FROM scores WHERE score_date = ?",
            (cutoff_session,),
        )
    }
    flags: dict[int, list[dict]] = {}
    for row in conn.execute(
        "SELECT security_id, flag_code, severity FROM risk_flags WHERE as_of_date = ?",
        (cutoff_session,),
    ):
        flags.setdefault(int(row["security_id"]), []).append(dict(row))

    dilution = {
        int(r["security_id"]): dict(r)
        for r in conn.execute(
            "SELECT security_id, dilution_score, is_disqualified FROM dilution_signals "
            "WHERE as_of_date <= ? AND as_of_date = ("
            "  SELECT MAX(as_of_date) FROM dilution_signals d2 "
            "  WHERE d2.security_id = dilution_signals.security_id AND d2.as_of_date <= ?)",
            (cutoff_session, cutoff_session),
        )
    }
    applicable = {
        int(r["security_id"]): (int(r["model_applicable"]), int(r["inputs_complete"]))
        for r in conn.execute(
            "SELECT d.security_id, d.model_applicable, d.inputs_complete FROM derived_fundamentals d "
            "JOIN (SELECT security_id, MAX(period_end) pe FROM derived_fundamentals "
            "      WHERE knowledge_date <= ? GROUP BY security_id) m "
            "  ON m.security_id = d.security_id AND m.pe = d.period_end "
            "WHERE d.knowledge_date <= ? "
            "GROUP BY d.security_id",
            (cutoff_utc, cutoff_utc),
        )
    }

    exits, gaps, open_horizons = position_state(conn, cutoff_session)

    rows: list[R.Row] = []
    faults: list[dict] = []
    unknown_risk_data: list[dict] = []
    for security in universe_rows(conn, pool_versions):
        security_id = int(security["security_id"])
        score = scores.get(security_id)
        my_flags = flags.get(security_id, [])
        dil = dilution.get(security_id)
        has_risk_flags = security_id in flags
        model_applicable, inputs_complete = applicable.get(security_id, (0, 0))

        if dil is None or not has_risk_flags:
            missing = [
                name for name, present in (
                    ("dilution_signals", dil is not None),
                    ("risk_flags", has_risk_flags),
                ) if not present
            ]
            unknown_risk_data.append({
                "security_id": security_id,
                "symbol": security["symbol"] or str(security_id),
                "composite": (score or {}).get("composite_score"),
                "rank": (score or {}).get("rank"),
                "missing": missing,
            })
            continue

        high = {f["flag_code"] for f in my_flags if f["severity"] == "high"}
        rows.append(R.Row(
            security_id=security_id,
            symbol=security["symbol"] or str(security_id),
            cohort_id=(score or {}).get("cohort_id") or "SIC-UNKNOWN",
            rankable=bool(score and int(score["rankable"]) == 1),
            model_applicable=bool(model_applicable),
            composite=(score or {}).get("composite_score"),
            rank=(score or {}).get("rank"),
            quality=(score or {}).get("quality_score"),
            inputs_complete=inputs_complete,
            dilution_score=float(dil["dilution_score"]),
            dilution_disqualified=bool(int(dil["is_disqualified"]) == 1),
            high_going_concern="going_concern" in high,
            high_dilution_flags=tuple(sorted(high & set(R.DILUTION_DISQUALIFY_FLAGS))),
            last_exit_session=exits.get(security_id),
            last_gap_cancel_session=gaps.get(security_id),
            open_horizons=tuple(sorted(open_horizons.get(security_id, ()))),
        ))
    return rows, faults, scores, unknown_risk_data


def position_state(conn, cutoff_session: str):
    """Most recent exit, most recent gap cancellation, and open horizons.

    Reads paper_positions and cancelled_entries, not `positions` -- migration
    013 dropped `positions` (F11's own comment: "pipeline/selection reads
    paper_positions"), but this function was never updated to match, which
    meant every selection run from that point on would raise
    `OperationalError: no such table: positions` the moment it actually
    executed. paper_positions carries no security_id of its own, only
    candidate_id, so it is joined through research_candidates. A gap
    cancellation never became a position at all, so it lives in its own
    candidate-keyed cancelled_entries table instead of a paper_positions
    status -- there is no 'gap_cancelled' status; the CHECK constraint only
    allows open/closed/pending_resolution.
    """
    exits: dict[int, str] = {}
    gaps: dict[int, str] = {}
    open_horizons: dict[int, set] = {}
    for row in conn.execute(
        "SELECT rc.security_id AS security_id, pp.horizon_days, pp.status, pp.exit_date "
        "FROM paper_positions pp JOIN research_candidates rc ON rc.candidate_id = pp.candidate_id"
    ):
        security_id = int(row["security_id"])
        if row["status"] == "open":
            open_horizons.setdefault(security_id, set()).add(int(row["horizon_days"]))
        elif row["status"] == "closed" and row["exit_date"] and row["exit_date"] <= cutoff_session:
            if exits.get(security_id) is None or row["exit_date"] > exits[security_id]:
                exits[security_id] = row["exit_date"]

    for row in conn.execute(
        "SELECT rc.security_id AS security_id, ce.cancelled_at "
        "FROM cancelled_entries ce JOIN research_candidates rc ON rc.candidate_id = ce.candidate_id"
    ):
        security_id = int(row["security_id"])
        # cancelled_at is a UTC timestamp; a cancellation is decided at the next
        # regular session's open, which falls on the same UTC calendar date as
        # that session for every US market hour, so truncating to the date is
        # the trading-date equivalent closed_on/exit_date already store.
        cancelled_date = str(row["cancelled_at"])[:10]
        if cancelled_date <= cutoff_session:
            if gaps.get(security_id) is None or cancelled_date > gaps[security_id]:
                gaps[security_id] = cancelled_date
    return exits, gaps, open_horizons


def accessions_from_score(explanation: dict) -> list[str]:
    """Every accession the score's explanation cites, deduplicated."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("accession", "source_accession") and isinstance(value, str):
                    found.add(value)
                elif key.endswith("_accession") and isinstance(value, str):
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(explanation)
    return sorted(a for a in found if a and not a.startswith("ledger:") and a != "none")


def check_evidence_cutoff(conn, accessions: list[str], cutoff_utc: str) -> list[str]:
    """Accessions that are too new, or whose acceptance time is unresolvable."""
    offending: list[str] = []
    for accession in accessions:
        row = conn.execute(
            "SELECT MAX(accepted_at) AS accepted FROM ("
            "  SELECT accepted_at FROM filings WHERE accession_no = ?"
            "  UNION ALL SELECT accepted_at FROM xbrl_facts WHERE accession_no = ?"
            "  UNION ALL SELECT accepted_at FROM insider_transactions WHERE accession_no = ?)",
            (accession, accession, accession),
        ).fetchone()
        accepted = row["accepted"] if row else None
        if accepted is None:
            offending.append(f"{accession} (acceptance time unresolvable)")
        elif accepted > cutoff_utc:
            offending.append(f"{accession} (accepted {accepted}, after the cutoff)")
    return offending


# ------------------------------------------------------------ frozen config lock


def verify_frozen_config_lock(conn, cfg: dict, running_hash: str) -> None:
    """Refuse to generate an OFFICIAL candidate if the running config does not
    match the exact bytes locked in frozen_config_lock (migration 021) for
    this strategy_version.

    This is narrower than, and additional to, config_loader's existing
    _governed_by/_version_digests check: that mechanism blocks LOADING the
    config at all if a governed value drifts without a version bump, but a
    config that bumps every version correctly can still drift from what was
    actually calibrated. An official candidate generated under a config no
    human actually locked in would carry a decision nobody reviewed, so this
    checks the whole-file hash, not just the governed subset, and only for
    runs that would produce official output (no --pool, no
    --provisional-threshold).
    """
    strategy_version = int(cfg["strategy_version"])
    row = conn.execute(
        "SELECT config_hash, calibration_report_id, locked_at FROM frozen_config_lock "
        "WHERE strategy_version = ?",
        (strategy_version,),
    ).fetchone()
    if row is None:
        raise SystemExit(
            "REFUSING to generate official candidates: no frozen_config_lock row "
            f"for strategy_version={strategy_version}. A calibration report and a "
            "lock row (migration 021) must exist for this exact strategy_version "
            "before an official selection run."
        )
    if running_hash != row["config_hash"]:
        raise SystemExit(
            "REFUSING to generate official candidates: config.frozen.json has "
            f"changed since it was locked for strategy_version={strategy_version}.\n"
            f"  locked config_hash:  {row['config_hash']}\n"
            f"  running config_hash: {running_hash}\n"
            f"  locked by calibration report {row['calibration_report_id']!r} "
            f"at {row['locked_at']}\n"
            "If this change is intentional: bump strategy_version, regenerate a "
            "calibration report over the new config, and insert a new "
            "frozen_config_lock row for the new version."
        )


# --------------------------------------------------------------------- books


def ensure_books(conn, cfg) -> dict[int, dict]:
    """Create the two books once, then read them. Never reset."""
    for horizon in cfg["horizons"]:
        conn.execute(
            "INSERT OR IGNORE INTO books (book_id, horizon_days, starting_nav, "
            "current_nav, open_position_count, strategy_version) VALUES (?, ?, ?, ?, 0, ?)",
            (BOOK_IDS.get(horizon, f"book-{horizon}d"), horizon,
             float(cfg["book_starting_nav"]), float(cfg["book_starting_nav"]),
             int(cfg["strategy_version"])),
        )
    return {
        int(r["horizon_days"]): dict(r)
        for r in conn.execute("SELECT * FROM books ORDER BY horizon_days")
    }


# ---------------------------------------------------------------------- main


def run_selection(conn, cfg, args) -> dict:
    cfg_hash = config_hash(args.config)

    # Startup check: an official run (real fixture population, no exploratory
    # override) must match the exact config that was locked in for this
    # strategy_version, checked before anything else runs.
    is_official_attempt = args.pool is None and args.provisional_threshold is None
    if is_official_attempt:
        verify_frozen_config_lock(conn, cfg, cfg_hash)

    session_dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM prices ORDER BY date")]
    if not session_dates:
        raise SystemExit("no price sessions in the dataset; selection cannot run")

    week = CAL.latest_complete_week(session_dates, args.as_of)
    if week is None:
        raise SystemExit(
            f"no provably complete trading week at or before {args.as_of}. The newest "
            f"session is {session_dates[-1]}, and a week is only complete once a "
            f"session in a later week exists."
        )
    cutoff_session = week.final_session
    cutoff_utc = CAL.session_close_utc(cutoff_session)
    horizons = list(cfg["horizons"])

    run_id = f"selection-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
        "VALUES (?, 'selection', ?, 'running', ?)",
        (run_id, utc_now(), CODE_VERSION),
    )

    # ---- freshness, pipeline only. Issuer-report age is a risk flag, not a gate.
    now_utc = utc_now()
    report = FR.check_pipeline_freshness(conn, cutoff_utc, now_utc, cfg)
    report.statuses.append(FR.check_latest_session_present(conn, cutoff_session))
    report.statuses.append(FR.check_form4_coverage(conn, cutoff_utc, now_utc, cfg))

    books = ensure_books(conn, cfg)
    capacity = {
        h: int(cfg["max_open_positions_per_horizon"]) - int(books[h]["open_position_count"])
        for h in horizons if h in books
    }

    rows, _faults, scores, unknown_risk_data = load_rows(
        conn, cutoff_session, cutoff_utc, session_dates, cfg, args.pool
    )

    threshold = cfg.get("composite_threshold")
    if threshold is None and args.provisional_threshold is not None:
        threshold = float(args.provisional_threshold)

    if not report.ok:
        # A stale source blocks EVERY new candidate. Nothing is selected and the
        # whole universe is logged with the failing sources named.
        detail = "; ".join(f"{s.source}: {s.detail}" for s in report.failures)
        result = R.SelectionResult()
        for row in rows:
            result.suppress(row, horizons, "stale_source", detail)
    else:
        exit_cutoff = CAL.sessions_back(session_dates, cutoff_session, int(cfg["exit_cooldown_days"]))
        gap_cutoff = CAL.sessions_back(session_dates, cutoff_session, int(cfg["gap_cancel_cooldown_days"]))
        result = R.select(
            rows, horizons=horizons, threshold=threshold,
            dilution_limit=float(cfg["dilution_disqualify"]),
            max_candidates=int(cfg["max_candidates_per_selection"]),
            max_per_cohort=int(cfg["max_per_cohort"]),
            exit_cutoff_session=exit_cutoff, gap_cutoff_session=gap_cutoff,
            exit_cooldown_days=int(cfg["exit_cooldown_days"]),
            gap_cooldown_days=int(cfg["gap_cancel_cooldown_days"]),
            book_capacity=dict(capacity),
        )

    # Securities with no dilution_signals or risk_flags row on file: eligibility
    # cannot be honestly evaluated, so they are logged as suppressed (unknown)
    # rather than folded into `rows` as if they were screened clean. Logged
    # unconditionally, independent of the freshness branch above -- missing
    # screening data is a fact about the security, not about pipeline staleness.
    for item in unknown_risk_data:
        for horizon in horizons:
            result.suppressions.append(R.Suppression(
                item["security_id"], horizon, item["composite"], item["rank"],
                "dilution_or_riskflags_unknown",
                "no " + " or ".join(item["missing"]) + " row on file at this cutoff; "
                "eligibility cannot be honestly evaluated, so this security is excluded "
                "rather than assumed clean",
            ))

    health_snapshot = json.dumps({
        "checked_at_cutoff": cutoff_utc,
        "sources": report.as_json(),
        "source_health_table": [
            dict(r) for r in conn.execute("SELECT * FROM source_health ORDER BY source_name")
        ],
    })

    generated_at = utc_now()
    written, rejected = [], []
    for position, row in enumerate(result.selected, start=1):
        score = scores.get(row.security_id) or {}
        explanation = json.loads(score.get("explanation_json") or "{}")
        accessions = accessions_from_score(explanation)
        offending = check_evidence_cutoff(conn, accessions, cutoff_utc)
        if offending:
            # The score itself leaned on evidence from after the cutoff, so this
            # security cannot become an official candidate.
            for horizon in horizons:
                result.suppressions.append(R.Suppression(
                    row.security_id, horizon, row.composite, row.rank, "stale_source",
                    "evidence cutoff violated: " + "; ".join(offending),
                ))
            rejected.append(row.symbol)
            continue

        bars = adjusted_series(conn, row.security_id)
        usable = [b for b in bars if b.date <= cutoff_session and b.close is not None]
        if not usable:
            for horizon in horizons:
                result.suppressions.append(R.Suppression(
                    row.security_id, horizon, row.composite, row.rank, "stale_source",
                    f"no adjusted close at or before {cutoff_session}",
                ))
            rejected.append(row.symbol)
            continue

        candidate = {
            "candidate_id": candidate_identity(
                row.security_id, cutoff_utc, cfg["strategy_version"],
                cfg["selection_rule_version"],
            ),
            "security_id": row.security_id,
            "generated_at": generated_at,
            "data_cutoff_at": cutoff_utc,
            "snapshot_id": explanation.get("snapshot_id"),
            "pipeline_run_id": run_id,
            "strategy_version": int(cfg["strategy_version"]),
            "config_hash": cfg_hash,
            "code_version": (
                CODE_VERSION
                + ("+provisional" if cfg.get("composite_threshold") is None else "")
                + (f"+pool[{','.join(sorted(args.pool))}]" if args.pool else "")
            ),
            "selection_rule_version": int(cfg["selection_rule_version"]),
            "mapping_version": str(score.get("mapping_version") or cfg["mapping_version"]),
            "price_dataset_version": score.get("price_dataset_version"),
            "price_snapshot_hash": score.get("price_snapshot_hash"),
            "source_health_snapshot_json": health_snapshot,
            "score_snapshot_json": json.dumps({
                "composite_score": row.composite, "rank": row.rank,
                "value_score": explanation.get("components", {}).get("value", {}).get("score"),
                "quality_score": row.quality,
                "momentum_score": explanation.get("components", {}).get("momentum", {}).get("score"),
                "cohort_id": row.cohort_id,
                "threshold_applied": threshold,
                "threshold_is_provisional": cfg.get("composite_threshold") is None,
                "explanation": explanation,
            }),
            "accessions_used_json": json.dumps(accessions),
            "composite_at_generation": float(row.composite),
            "rank_at_generation": position,
            "signal_close": float(usable[-1].close),
            "atr_value": atr(usable, int(cfg["atr_window"])),
            "atr_window": int(cfg["atr_window"]),
            "price_data_cutoff": usable[-1].date,
            "entry_rule": ENTRY_RULE,
            "gap_limit_atr": float(cfg["gap_cancel_atr"]),
        }
        candidate["row_hash"] = row_hash(candidate)
        written.append(candidate)

    columns = list(HASHED_COLUMNS) + ["row_hash"]
    for candidate in written:
        existing = conn.execute(
            "SELECT row_hash FROM research_candidates WHERE candidate_id = ?",
            (candidate["candidate_id"],),
        ).fetchone()
        if existing is None:
            conn.execute(
                f"INSERT INTO research_candidates ({','.join(columns)}) "
                f"VALUES ({','.join('?' * len(columns))})",
                [candidate[c] for c in columns],
            )
        elif existing["row_hash"] != candidate["row_hash"]:
            # Same week, same security, different decision. Append-only means we
            # cannot overwrite, and pretending nothing happened would hide it.
            raise SystemExit(
                f"candidate {candidate['candidate_id']} already exists with a "
                f"different row_hash. The same selection week has produced two "
                f"different decisions, which means an input changed after the "
                f"cutoff. Investigate before re-running."
            )

    for suppression in result.suppressions:
        conn.execute(
            'INSERT OR REPLACE INTO suppressed_signals (run_id, security_id, '
            'horizon_days, composite, "rank", suppression_reason, detail) '
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, suppression.security_id, suppression.horizon_days,
             suppression.composite, suppression.rank, suppression.reason,
             suppression.detail),
        )

    conn.execute(
        "UPDATE pipeline_runs SET status=?, finished_at=?, records_written=? WHERE run_id=?",
        ("success" if report.ok else "partial", utc_now(), len(written), run_id),
    )
    return {
        "run_id": run_id, "week": week, "cutoff_session": cutoff_session,
        "cutoff_utc": cutoff_utc, "report": report, "result": result,
        "written": written, "rejected": rejected, "books": ensure_books(conn, cfg),
        "threshold": threshold, "rows": rows,
    }


def format_report(outcome: dict, cfg) -> str:
    week = outcome["week"]
    lines = [
        "",
        f"SELECTION WEEK  {week.year}-W{week.week:02d}  "
        f"{week.first_session} .. {week.final_session}  ({len(week.sessions)} sessions)",
        f"EVIDENCE CUTOFF {outcome['cutoff_utc']}  (regular close of {outcome['cutoff_session']})",
        f"RUN             {outcome['run_id']}",
        f"THRESHOLD       {outcome['threshold']}"
        + ("  [PROVISIONAL - config value is still null]"
           if cfg.get("composite_threshold") is None and outcome["threshold"] is not None
           else ""),
        "",
        "FRESHNESS",
    ]
    for status in outcome["report"].statuses:
        lines.append(f"  {'ok  ' if status.ok else 'FAIL'} {status.source:<28}{status.detail}")

    lines += ["", f"CANDIDATES ({len(outcome['written'])})"]
    if outcome["written"]:
        lines.append(f"  {'#':>2}  {'SYM':<7}{'COMPOSITE':>11}{'CLOSE':>10}{'ATR':>9}  COHORT")
        for candidate in outcome["written"]:
            symbol = next(
                r.symbol for r in outcome["rows"] if r.security_id == candidate["security_id"]
            )
            atr_text = "—" if candidate["atr_value"] is None else f"{candidate['atr_value']:.3f}"
            cohort = json.loads(candidate["score_snapshot_json"])["cohort_id"]
            lines.append(
                f"  {candidate['rank_at_generation']:>2}  {symbol:<7}"
                f"{candidate['composite_at_generation']:>11.4f}"
                f"{candidate['signal_close']:>10.2f}{atr_text:>9}  {cohort}"
            )
    else:
        lines.append("  none")

    by_reason: dict[str, set] = {}
    for suppression in outcome["result"].suppressions:
        by_reason.setdefault(suppression.reason, set()).add(suppression.security_id)
    lines += ["", "SUPPRESSION LOG  (unique securities per reason; rows are per horizon)"]
    for reason in sorted(by_reason):
        lines.append(f"  {reason:<32}{len(by_reason[reason]):>3}")
    lines.append(
        f"  {'TOTAL rows logged':<32}{len(outcome['result'].suppressions):>3}"
    )

    lines += ["", "BOOKS"]
    for horizon, book in sorted(outcome["books"].items()):
        lines.append(
            f"  {book['book_id']:<10}horizon {horizon:>3}d  "
            f"starting NAV {book['starting_nav']:>12,.2f}  "
            f"current NAV {book['current_nav']:>12,.2f}  "
            f"open positions {book['open_position_count']:>3}"
        )
    lines.append(
        "  The two books are SEPARATE strategy variants over the SAME candidates. "
        f"They are not two independent observations: this run produced "
        f"{len(outcome['written'])} unique originating candidate(s)."
    )
    return "\n".join(lines)


def verify(conn) -> list[str]:
    """Recompute every stored row_hash. A mismatch means the row was edited."""
    problems = []
    columns = list(HASHED_COLUMNS) + ["row_hash"]
    for row in conn.execute(f"SELECT {','.join(columns)} FROM research_candidates"):
        stored = dict(row)
        expected = row_hash(stored)
        if expected != stored["row_hash"]:
            problems.append(
                f"{stored['candidate_id']}: stored {stored['row_hash'][:16]}, "
                f"recomputed {expected[:16]} - this record has been edited and is "
                f"NON-OFFICIAL"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly research-candidate selection")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument(
        "--provisional-threshold", type=float, default=None,
        help="exercise the rule while composite_threshold is still null. Candidates "
             "are stamped code_version '+provisional' and are NOT official.",
    )
    parser.add_argument("--verify", action="store_true",
                        help="recompute every stored row_hash and report tampering")
    parser.add_argument(
        "--pool", action="append", default=None,
        help="select over a universe_candidate_pool version instead of the Phase F "
        "fixture; repeatable to combine pools. Candidates from a pool-scoped run are "
        "stamped code_version '+pool[...]' and are NOT official, matching every other "
        "Phase S pool-scoped output",
    )
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    conn = migrate.connect(Path(args.db))
    try:
        if args.verify:
            problems = verify(conn)
            print("\n".join(problems) if problems else
                  "every stored candidate's row_hash matches; none has been edited")
            return 1 if problems else 0

        conn.execute("BEGIN")
        outcome = run_selection(conn, cfg, args)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        if not args.verify:
            pass
    try:
        print(format_report(outcome, cfg))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
