"""S3: calibration report -- score distribution and candidate-rate frequency.

NO RETURN DATA. Nothing here reads paper_positions.exit_price, gross_pnl,
net_pnl, pnl_pct, benchmark_positions, or anything from pipeline/execution or
pipeline/riskflags' price-after-selection outcomes. Threshold selection is a
signal-FREQUENCY decision only: this report answers "how often would a
candidate appear at this threshold", never "what would have happened to it".

The candidate-rate simulation reuses selection.rules.select() unmodified --
the SAME deterministic rule F10 uses for a real weekly run, applied
hypothetically at each threshold to today's already-computed scores. It is a
single point-in-time simulation, not a historical backtest: the system has no
history of weekly score snapshots to replay, only today's. That limitation is
reported, not hidden.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from selection import rules as R  # noqa: E402

# The brief's own sweep: 50 to 90 in steps of 5.
THRESHOLD_SWEEP = list(range(50, 91, 5))

SCALAR_METRICS = [
    "pe", "pb", "ev_ebitda", "fcf_yield", "roic", "interest_coverage",
    "debt_ebitda", "current_ratio", "gross_margin", "revenue_growth_yoy",
    "shares_outstanding",
]

COMPONENT_FIELDS = ["value_score", "quality_score", "momentum_score", "insider_bonus",
                    "dilution_penalty", "composite_score"]


def latest_score_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(score_date) AS d FROM scores").fetchone()
    return row["d"] if row and row["d"] else None


def load_rows(conn, score_date: str) -> list[R.Row]:
    """Every scored security as the selection rule sees it, for whichever
    population (fixture or pool) was scored on this date. No fixture_manifest
    dependency: `scores` already carries every security actually scored."""
    scores = {
        int(r["security_id"]): dict(r)
        for r in conn.execute(
            'SELECT security_id, composite_score, "rank", quality_score, rankable, '
            "cohort_id FROM scores WHERE score_date = ?",
            (score_date,),
        )
    }
    flags: dict[int, set[str]] = {}
    for row in conn.execute(
        "SELECT security_id, flag_code FROM risk_flags WHERE severity = 'high' AND as_of_date = ?",
        (score_date,),
    ):
        flags.setdefault(int(row["security_id"]), set()).add(row["flag_code"])

    dilution = {
        int(r["security_id"]): dict(r)
        for r in conn.execute(
            "SELECT security_id, dilution_score, is_disqualified FROM dilution_signals "
            "WHERE as_of_date <= ? AND as_of_date = ("
            "  SELECT MAX(as_of_date) FROM dilution_signals d2 "
            "  WHERE d2.security_id = dilution_signals.security_id AND d2.as_of_date <= ?)",
            (score_date, score_date),
        )
    }

    listings = {
        int(r["security_id"]): r["symbol"]
        for r in conn.execute(
            "SELECT security_id, symbol FROM listings WHERE valid_to IS NULL"
        )
    }

    rows: list[R.Row] = []
    for security_id, score in scores.items():
        high = flags.get(security_id, set())
        dil = dilution.get(security_id)
        rows.append(R.Row(
            security_id=security_id,
            symbol=listings.get(security_id) or str(security_id),
            cohort_id=score.get("cohort_id") or "SIC-UNKNOWN",
            rankable=bool(score["rankable"]),
            model_applicable=True,  # scores table already excludes non-applicable securities
            composite=score.get("composite_score"),
            rank=score.get("rank"),
            quality=score.get("quality_score"),
            # Only used for tie-break ordering in sort_key, never for
            # eligibility, so a rankable proxy is sufficient here.
            inputs_complete=1 if score.get("rankable") else 0,
            dilution_score=float(dil["dilution_score"]) if dil else 0.0,
            dilution_disqualified=bool(dil and int(dil["is_disqualified"]) == 1),
            high_going_concern="going_concern" in high,
            high_dilution_flags=tuple(sorted(high & set(R.DILUTION_DISQUALIFY_FLAGS))),
            # No execution history for S1/S2 pool securities yet, so cooldowns
            # never fire here -- correct, not an omission: nothing has traded.
            last_exit_session=None,
            last_gap_cancel_session=None,
            open_horizons=(),
        ))
    return rows


def histogram(values: list[float], bucket_size: float, lo: float = 0, hi: float = 100) -> list[dict]:
    buckets: dict[int, int] = {}
    n_buckets = int((hi - lo) / bucket_size)
    for v in values:
        if v is None:
            continue
        idx = min(n_buckets - 1, max(0, int((v - lo) / bucket_size)))
        buckets[idx] = buckets.get(idx, 0) + 1
    return [
        {"bucket_start": lo + i * bucket_size, "bucket_end": lo + (i + 1) * bucket_size,
         "count": buckets.get(i, 0)}
        for i in range(n_buckets)
    ]


def rankable_vs_withheld(conn, score_date: str) -> dict:
    rows = conn.execute(
        "SELECT rankable, withhold_reason, COUNT(*) c FROM scores WHERE score_date = ? "
        "GROUP BY rankable, withhold_reason", (score_date,),
    ).fetchall()
    rankable = sum(r["c"] for r in rows if r["rankable"])
    withheld = [
        {"reason": r["withhold_reason"] or "(none recorded)", "count": r["c"]}
        for r in rows if not r["rankable"]
    ]
    withheld.sort(key=lambda x: -x["count"])
    return {"rankable": rankable, "withheld_total": sum(w["count"] for w in withheld),
            "withheld_by_reason": withheld[:20]}


def component_distributions(conn, score_date: str) -> dict[str, list[dict]]:
    rows = conn.execute(
        f"SELECT {', '.join(COMPONENT_FIELDS)} FROM scores WHERE score_date = ? AND rankable = 1",
        (score_date,),
    ).fetchall()
    out = {}
    for field in COMPONENT_FIELDS:
        values = [r[field] for r in rows if r[field] is not None]
        out[field] = histogram(values, bucket_size=10 if field != "insider_bonus" else 1,
                                hi=100 if field != "insider_bonus" else 10)
    return out


def submetric_distributions(conn, score_date: str) -> dict[str, dict]:
    """Percentile distribution per submetric, read from each score's stored
    explanation_json -- the same JSON /security/[id] renders, nothing
    recomputed."""
    rows = conn.execute(
        "SELECT explanation_json FROM scores WHERE score_date = ? AND rankable = 1 "
        "AND explanation_json IS NOT NULL",
        (score_date,),
    ).fetchall()
    collected: dict[str, list[float]] = {}
    valid_counts: dict[str, int] = {}
    total = 0
    for row in rows:
        total += 1
        try:
            explanation = json.loads(row["explanation_json"])
        except (TypeError, ValueError):
            continue
        for component in ("value", "quality", "momentum"):
            for name, sub in (explanation.get(component, {}).get("submetrics") or {}).items():
                key = f"{component}.{name}"
                if sub.get("valid"):
                    valid_counts[key] = valid_counts.get(key, 0) + 1
                    pct = sub.get("percentile")
                    if pct is not None:
                        collected.setdefault(key, []).append(pct)
    return {
        key: {
            "valid_count": valid_counts.get(key, 0),
            "total": total,
            "percentile_histogram": histogram(values, bucket_size=10),
        }
        for key, values in collected.items()
    }


def cohort_and_metric_coverage(conn, score_date: str) -> dict:
    cohorts = conn.execute(
        "SELECT cohort_id, COUNT(*) c FROM scores WHERE score_date = ? AND cohort_id IS NOT NULL "
        "GROUP BY cohort_id ORDER BY c DESC", (score_date,),
    ).fetchall()
    security_ids = [r["security_id"] for r in conn.execute(
        "SELECT security_id FROM scores WHERE score_date = ?", (score_date,)
    )]
    metric_counts = {}
    if security_ids:
        placeholders = ",".join("?" for _ in security_ids)
        for metric in SCALAR_METRICS:
            row = conn.execute(
                f"SELECT COUNT(*) c FROM derived_fundamentals WHERE security_id IN ({placeholders}) "
                f"AND {metric} IS NOT NULL AND knowledge_date = ("
                f"  SELECT MAX(d2.knowledge_date) FROM derived_fundamentals d2 "
                f"  WHERE d2.security_id = derived_fundamentals.security_id)",
                security_ids,
            ).fetchone()
            metric_counts[metric] = row["c"]
    return {
        "cohort_sizes": [{"cohort_id": r["cohort_id"], "count": r["c"]} for r in cohorts],
        "metric_valid_counts": metric_counts,
        "total_scored": len(security_ids),
    }


def simulate_candidate_rate(rows: list[R.Row], cfg: dict) -> list[dict]:
    """select() applied hypothetically at each threshold -- the SAME rule
    F10 runs for real, with cooldowns/book-capacity naturally inert (no
    execution history exists for these securities yet). No return data enters
    this function or anything it calls."""
    horizons = cfg["horizons"]
    book_capacity = {h: cfg["max_open_positions_per_horizon"] for h in horizons}
    results = []
    for threshold in THRESHOLD_SWEEP:
        outcome = R.select(
            rows, horizons=horizons, threshold=float(threshold),
            dilution_limit=float(cfg["dilution_disqualify"]),
            max_candidates=int(cfg["max_candidates_per_selection"]),
            max_per_cohort=int(cfg["max_per_cohort"]),
            exit_cutoff_session=None, gap_cutoff_session=None,
            exit_cooldown_days=int(cfg["exit_cooldown_days"]),
            gap_cooldown_days=int(cfg["gap_cancel_cooldown_days"]),
            book_capacity=book_capacity,
        )
        n = len(outcome.selected)
        cohort_dist: dict[str, int] = {}
        for row in outcome.selected:
            cohort_dist[row.cohort_id] = cohort_dist.get(row.cohort_id, 0) + 1

        # Pure arithmetic projection from the candidate rate and the
        # protocol's own fixed max-hold parameter (config, not an outcome) --
        # never anything about what a position actually did.
        weeks_per_horizon = {}
        for h in horizons:
            if n == 0:
                weeks_per_horizon[h] = None
            else:
                weeks_to_generate = 100 / n
                trading_days_per_week = 5
                weeks_to_close_last = h / trading_days_per_week
                weeks_per_horizon[h] = round(weeks_to_generate + weeks_to_close_last, 1)

        results.append({
            "threshold": threshold,
            "candidates_per_week": n,
            "suppressed": len(outcome.suppressions),
            "cohort_distribution": cohort_dist,
            "estimated_weeks_to_100_closed": weeks_per_horizon,
        })
    return results


def build_report(conn, cfg: dict) -> dict:
    score_date = latest_score_date(conn)
    if score_date is None:
        return {"score_date": None, "empty": True}

    rows = load_rows(conn, score_date)
    return {
        "score_date": score_date,
        "empty": False,
        "composite_histogram": histogram(
            [r.composite for r in rows if r.rankable and r.composite is not None], bucket_size=10
        ),
        "component_distributions": component_distributions(conn, score_date),
        "submetric_distributions": submetric_distributions(conn, score_date),
        "rankable_vs_withheld": rankable_vs_withheld(conn, score_date),
        "cohort_and_metric_coverage": cohort_and_metric_coverage(conn, score_date),
        "candidate_rate_simulation": simulate_candidate_rate(rows, cfg),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import uuid
    from datetime import datetime, timezone

    import migrate
    from config_loader import load_config
    from scoring.compute import config_hash

    parser = argparse.ArgumentParser(description="S3 calibration report")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    args = parser.parse_args(argv)

    conn = migrate.connect(Path(args.db))
    try:
        report = build_report(conn, dict(load_config()))
        if not report.get("empty"):
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO calibration_reports (report_id, computed_at, score_date, "
                "config_hash, report_json) VALUES (?, ?, ?, ?, ?)",
                (
                    f"calib-{uuid.uuid4().hex[:12]}",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    report["score_date"],
                    config_hash(),
                    json.dumps(report, default=str),
                ),
            )
            conn.execute("COMMIT")
    finally:
        conn.close()

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
