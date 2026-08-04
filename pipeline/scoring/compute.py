"""Compute composite scores for the fixture.

Order of operations, and why it is this order:

  1. Build (or reuse) the OFFICIAL universe snapshot for the score date. Every
     comparison population in the run is drawn from that one snapshot, so two
     securities scored in the same run are always ranked against the same set.
     Without a fixed snapshot, "percentile" means nothing -- the number would
     depend on which securities happened to have data that day.

  2. Collect every input for every security at one knowledge cutoff. Point-in-
     time correctness comes from filtering derived_fundamentals on
     knowledge_date, never from the latest_fundamentals view.

  3. Build the metric populations, then score. Populations must exist before any
     security is scored, because a percentile is a statement about a set.

  4. Rank, then write. Withheld securities are written too, with NULL scores and
     a reason -- a security missing from the table is indistinguishable from one
     that was never attempted.

No component weight is ever redistributed to another component. Renormalisation
happens strictly inside Value (across its valid submetrics) and inside Quality's
non-Piotroski 0.60 share. A NULL component withholds the whole security.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
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
from scoring import insider as INS  # noqa: E402
from scoring import momentum as MOM  # noqa: E402
from scoring import quality as QUAL  # noqa: E402
from scoring.cohorts import cohort_for_sic  # noqa: E402
from scoring.percentile import (  # noqa: E402
    RankedMetric,
    WeightedComponent,
    blend,
    blend_weight,
    invert_if_lower_is_better,
    percentile,
)
from sec.payload_store import utc_now  # noqa: E402

CODE_VERSION = "composite/v1"
BENCHMARK_SYMBOL = "SPY"

COMPONENT_WEIGHTS = {"value": 0.30, "quality": 0.30, "momentum": 0.30}
VALUE_WEIGHTS = {"pe": 0.25, "pb": 0.25, "ev_ebitda": 0.25, "fcf_yield": 0.25}
MIN_VALID_VALUE_SUBMETRICS = 3

MARKET_POPULATION = "official universe snapshot: operating common stock"


def config_hash(path: Path | str = DEFAULT_CONFIG_PATH) -> str:
    """sha256 of the frozen config file's bytes. Pins every parameter at once."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in str(value)[:10].split("-"))
    return date(year, month, day)


# ------------------------------------------------------------------- universe


def resolve_score_date(conn: sqlite3.Connection, as_of: str) -> str | None:
    """The last ET trading date at or before as_of. Scores belong to sessions."""
    row = conn.execute("SELECT MAX(date) AS d FROM prices WHERE date <= ?", (as_of,)).fetchone()
    return row["d"] if row and row["d"] else None


def universe_rows(conn: sqlite3.Connection, pool_versions: list[str] | None = None) -> list[dict]:
    """Every fixture security (default) or every security in the named
    candidate pool version(s), with the fields the universe decision needs."""
    if pool_versions:
        placeholders = ",".join("?" for _ in pool_versions)
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT s.security_id, s.cik, s.name, s.security_type, s.sic_code,
                       COALESCE(l.symbol, p.symbol_at_discovery) AS symbol
                  FROM universe_candidate_pool p
                  JOIN securities s ON s.security_id = p.security_id
                  LEFT JOIN listings l ON l.security_id = p.security_id AND l.valid_to IS NULL
                 WHERE p.pool_version IN ({placeholders})
                 GROUP BY s.security_id
                 ORDER BY s.security_id
                """,
                pool_versions,
            )
        ]
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.security_id, s.cik, s.name, s.security_type, s.sic_code,
                   MIN(f.symbol_at_selection) AS symbol
              FROM fixture_manifest f
              JOIN securities s ON s.security_id = f.security_id
             GROUP BY s.security_id
             ORDER BY s.security_id
            """
        )
    ]


def model_applicable_map(conn: sqlite3.Connection, cutoff: str) -> dict[int, int]:
    rows = conn.execute(
        """
        SELECT security_id, MAX(period_end) AS period_end
          FROM derived_fundamentals
         WHERE knowledge_date <= ?
         GROUP BY security_id
        """,
        (cutoff,),
    ).fetchall()
    result: dict[int, int] = {}
    for row in rows:
        applicable = conn.execute(
            """
            SELECT model_applicable FROM derived_fundamentals
             WHERE security_id = ? AND period_end = ? AND knowledge_date <= ?
             ORDER BY knowledge_date DESC LIMIT 1
            """,
            (row["security_id"], row["period_end"], cutoff),
        ).fetchone()
        if applicable is not None:
            result[int(row["security_id"])] = int(applicable["model_applicable"])
    return result


def universe_decision(row: dict, applicable: dict[int, int]) -> tuple[str, str | None]:
    """(status, exclusion_reason) for one security in the official snapshot."""
    if row["security_type"] == "unknown":
        return "excluded", "classification is unknown, which is never rankable"
    if row["security_type"] != "common_stock":
        return "excluded", f"security type is {row['security_type']}, not common stock"
    flag = applicable.get(int(row["security_id"]))
    if flag is None:
        return "excluded", "no derived fundamentals at the knowledge cutoff"
    if flag == 0:
        return "excluded", (
            "model not supported: SIC division H (finance, insurance, real estate) "
            "carries model_applicable = 0 from F5"
        )
    return "included", None


def ensure_snapshot(
    conn: sqlite3.Connection, score_date: str, cutoff: str, cfg_hash: str, run_id: str,
    pool_versions: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Create the official universe snapshot for this score date, or reuse it.

    F1-F7 never populated universe_snapshot_runs, but F8 requires that comparison
    sets come from "the same official universe snapshot". Rather than quietly
    comparing against whatever rows a query happened to return, the snapshot is
    materialised here from the fixture manifest and marked official, so every
    percentile in the run cites a snapshot_id that can be re-read later.
    """
    # Pool-scoped runs are a distinct population from the fixture at the same
    # score_date/config_hash, so the reuse lookup and snapshot_id must be
    # pool-aware too, or a pool run would silently find and reuse the
    # fixture's official snapshot.
    pool_tag = ",".join(sorted(pool_versions)) if pool_versions else "fixture"
    rules_version = f"F8-scoring/v1[{pool_tag}]"
    is_official = 0 if pool_versions else 1
    existing = conn.execute(
        """
        SELECT snapshot_id FROM universe_snapshot_runs
         WHERE effective_at = ? AND config_hash = ? AND rules_version = ?
        """,
        (score_date, cfg_hash, rules_version),
    ).fetchone()

    rows = universe_rows(conn, pool_versions)
    applicable = model_applicable_map(conn, cutoff)
    decided = []
    for row in rows:
        status, reason = universe_decision(row, applicable)
        cohort_id, cohort_name = cohort_for_sic(row["sic_code"])
        decided.append({**row, "status": status, "exclusion_reason": reason,
                        "cohort_id": cohort_id, "cohort_label": cohort_name})

    if existing:
        return existing["snapshot_id"], decided

    pool_digest = hashlib.sha256(pool_tag.encode("utf-8")).hexdigest()[:8]
    snapshot_id = f"universe-{score_date}-{cfg_hash[:8]}-{pool_digest}"
    conn.execute(
        """
        INSERT INTO universe_snapshot_runs
            (snapshot_id, effective_at, rules_version, config_hash, run_id,
             security_count, is_official)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (snapshot_id, score_date, rules_version, cfg_hash, run_id,
         sum(1 for d in decided if d["status"] == "included"), is_official),
    )
    for item in decided:
        conn.execute(
            """
            INSERT OR REPLACE INTO universe_snapshots
                (snapshot_id, security_id, snapshot_date, status, exclusion_reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (snapshot_id, item["security_id"], score_date, item["status"],
             item["exclusion_reason"]),
        )
    return snapshot_id, decided


# --------------------------------------------------------------------- inputs


def fundamentals_at(conn: sqlite3.Connection, security_id: int, cutoff: str):
    """Newest fiscal period knowable at the cutoff, at its newest knowledge state."""
    period = conn.execute(
        """
        SELECT MAX(period_end) AS period_end FROM derived_fundamentals
         WHERE security_id = ? AND knowledge_date <= ?
        """,
        (security_id, cutoff),
    ).fetchone()
    if not period or not period["period_end"]:
        return None, None
    row = conn.execute(
        """
        SELECT * FROM derived_fundamentals
         WHERE security_id = ? AND period_end = ? AND knowledge_date <= ?
         ORDER BY knowledge_date DESC LIMIT 1
        """,
        (security_id, period["period_end"], cutoff),
    ).fetchone()
    prior = conn.execute(
        """
        SELECT MAX(period_end) AS period_end FROM derived_fundamentals
         WHERE security_id = ? AND period_end < ? AND knowledge_date <= ?
        """,
        (security_id, period["period_end"], cutoff),
    ).fetchone()
    return row, (prior["period_end"] if prior else None)


def insider_inputs(conn: sqlite3.Connection, security_id: int, score_date: str,
                   cfg: dict, run: dict | None, attempted_ids: set[int]) -> tuple[list[dict], INS.Coverage]:
    purchases = [
        dict(row)
        for row in conn.execute(
            """
            SELECT accession_no, insider_cik, insider_name, officer_title,
                   transaction_date, filed_date, plan_status, shares,
                   price_per_share, total_value, shares_owned_after
              FROM scored_insider_purchases
             WHERE security_id = ? AND transaction_date IS NOT NULL
               AND transaction_date <= ?
             ORDER BY transaction_date DESC
            """,
            (security_id, score_date),
        )
    ]
    counts = conn.execute(
        """
        SELECT COUNT(DISTINCT CASE WHEN is_amendment = 0 THEN accession_no END) AS originals,
               COUNT(DISTINCT CASE WHEN is_amendment = 1 THEN accession_no END) AS amendments,
               MIN(filed_date) AS oldest
          FROM insider_transactions WHERE security_id = ?
        """,
        (security_id,),
    ).fetchone()

    window_start = (
        parse_date(score_date).toordinal() - INS.SIZE_WINDOW_DAYS
    )
    window_start_iso = date.fromordinal(window_start).isoformat()

    staleness = None
    if run and run["finished_at"]:
        finished = run["finished_at"][:10]
        staleness = (parse_date(score_date).toordinal() - parse_date(finished).toordinal()) * 24.0
        staleness = max(0.0, staleness)
    sla = (cfg.get("freshness_sla") or {}).get("filings")

    # "Attempted" means this security was part of whatever scoring population
    # (fixture, or an S2-orchestrated pool) the Form 4 ingest was actually run
    # against -- attempted_ids is that population's security_ids, built once
    # by the caller from the same `members` list score_universe already has,
    # rather than hardcoding fixture_manifest and silently excluding every
    # S1/S2 pool security from ever being scorable.
    attempted = security_id in attempted_ids

    coverage = INS.assess_coverage(
        attempted=attempted,
        run_ok=run is not None and run["status"] == "success",
        run_finished_at=run["finished_at"] if run else None,
        staleness_hours=staleness,
        sla_hours=float(sla) if sla is not None else None,
        originals_ingested=int(counts["originals"] or 0),
        amendments_ingested=int(counts["amendments"] or 0),
        oldest_filed_date=counts["oldest"],
        window_start=window_start_iso,
    )
    return purchases, coverage


# ----------------------------------------------------------------- populations


def build_populations(observations: dict[str, dict[int, float]],
                      members: list[dict]) -> dict:
    """Market and per-cohort valid observations, per metric."""
    cohort_of = {int(m["security_id"]): m["cohort_id"] for m in members}
    populations: dict[str, dict] = {}
    for metric, values in observations.items():
        market = sorted(values.values())
        cohorts: dict[str, list[float]] = {}
        for security_id, value in values.items():
            cohorts.setdefault(cohort_of[security_id], []).append(value)
        populations[metric] = {
            "market": market,
            "cohorts": {key: sorted(vals) for key, vals in cohorts.items()},
        }
    return populations


def rank_metric(metric: str, raw: float | None, security_id: int, cohort_id: str,
                populations: dict, cfg: dict, cutoff: str, snapshot_id: str,
                cohort_blended: bool, reason: str | None = None) -> RankedMetric:
    """One metric's blended, orientation-corrected percentile."""
    if raw is None:
        return RankedMetric(
            metric=metric, raw_value=None, valid=False, percentile=None,
            reason=reason or "not reported or invalid at the knowledge cutoff",
            market_population=MARKET_POPULATION, knowledge_cutoff=cutoff,
            snapshot_id=snapshot_id, cohort_population=cohort_id if cohort_blended else None,
        )

    market = populations[metric]["market"]
    cohort = populations[metric]["cohorts"].get(cohort_id, []) if cohort_blended else []
    market_pct = invert_if_lower_is_better(percentile(raw, market), metric)
    cohort_pct = (
        invert_if_lower_is_better(percentile(raw, cohort), metric)
        if cohort_blended else None
    )
    w = (
        blend_weight(len(cohort), int(cfg["cohort_blend_floor"]), int(cfg["cohort_blend_target"]))
        if cohort_blended else 0.0
    )
    final, blend_reason = blend(cohort_pct, market_pct, w)

    return RankedMetric(
        metric=metric, raw_value=raw, valid=final is not None, percentile=final,
        reason=blend_reason,
        market_population=MARKET_POPULATION, market_count=len(market),
        market_percentile=market_pct,
        cohort_population=cohort_id if cohort_blended else None,
        cohort_count=len(cohort), cohort_percentile=cohort_pct,
        blend_weight=w, knowledge_cutoff=cutoff, snapshot_id=snapshot_id,
    )


# --------------------------------------------------------------------- scoring


def score_universe(conn: sqlite3.Connection, score_date: str, cfg: dict,
                   cfg_hash: str, snapshot_id: str, members: list[dict],
                   cutoff: str) -> list[dict]:
    included = [m for m in members if m["status"] == "included"]
    attempted_ids = {int(m["security_id"]) for m in members}

    # 'orchestrate_form4' is S2's per-item orchestrated equivalent of a plain
    # 'insider' run (insider/ingest.py's own main()) -- same evidence, same
    # completeness guarantee when it finished with zero failures, just a
    # different stage name because it processes one security at a time
    # instead of one batch transaction.
    insider_run = conn.execute(
        """
        SELECT run_id, status, finished_at FROM pipeline_runs
         WHERE stage IN ('insider', 'orchestrate_form4') AND status = 'success'
         ORDER BY finished_at DESC LIMIT 1
        """
    ).fetchone()

    benchmark_id = conn.execute(
        """
        SELECT security_id FROM listings WHERE symbol = ? ORDER BY security_id LIMIT 1
        """,
        (BENCHMARK_SYMBOL,),
    ).fetchone()
    benchmark_bars = (
        adjusted_series(conn, int(benchmark_id["security_id"])) if benchmark_id else []
    )

    # ---- gather every input first; populations need all of them.
    gathered: dict[int, dict] = {}
    snapshot_material: list[str] = []
    for member in included:
        security_id = int(member["security_id"])
        fundamentals, prior_period = fundamentals_at(conn, security_id, cutoff)
        bars = adjusted_series(conn, security_id)
        momentum = MOM.compute_inputs(bars, benchmark_bars, security_id, score_date)
        purchases, coverage = insider_inputs(
            conn, security_id, score_date, cfg, insider_run, attempted_ids
        )
        dilution = conn.execute(
            """
            SELECT dilution_score, is_disqualified, as_of_date, shares_yoy_growth
              FROM dilution_signals WHERE security_id = ? AND as_of_date <= ?
             ORDER BY as_of_date DESC LIMIT 1
            """,
            (security_id, score_date),
        ).fetchone()
        gathered[security_id] = {
            "member": member,
            "fundamentals": fundamentals,
            "prior_period": prior_period,
            "momentum": momentum,
            "purchases": purchases,
            "coverage": coverage,
            "dilution": dilution,
        }
        if momentum.as_of_bar_date is not None:
            snapshot_material.append(
                f"{security_id}:{momentum.as_of_bar_date}:{momentum.bar_count}"
            )

    price_snapshot_hash = hashlib.sha256(
        "|".join(sorted(snapshot_material)).encode("utf-8")
    ).hexdigest()

    price_dataset_version = conn.execute(
        "SELECT MAX(dataset_version) AS v FROM price_dataset_versions"
    ).fetchone()["v"]

    # ---- fundamental observations, cohort-blended.
    fundamental_metrics = list(VALUE_WEIGHTS) + list(QUAL.NON_PIOTROSKI_WEIGHTS)
    observations: dict[str, dict[int, float]] = {m: {} for m in fundamental_metrics}
    for security_id, data in gathered.items():
        row = data["fundamentals"]
        if row is None:
            continue
        for metric in fundamental_metrics:
            value = row[metric] if metric in row.keys() else None
            if value is not None:
                observations[metric][security_id] = float(value)

    # ---- momentum observations, ranked against the whole operating universe.
    for metric in MOM.PERCENTILE_RANKED:
        observations[metric] = {
            security_id: data["momentum"].values[metric]
            for security_id, data in gathered.items()
            if data["momentum"].gate_passed and data["momentum"].values[metric] is not None
        }

    populations = build_populations(observations, included)

    # ---- insider size ratio S needs its own population: securities with S > 0.
    size_ratios: dict[int, tuple[float | None, dict]] = {}
    for security_id, data in gathered.items():
        market_cap = None
        row = data["fundamentals"]
        if row is not None and "market_cap" in row.keys():
            market_cap = row["market_cap"]
        size_ratios[security_id] = INS.size_ratio(
            data["purchases"], parse_date(score_date),
            None if market_cap is None else float(market_cap),
        )
    positive_s = sorted(
        value for value, _ in size_ratios.values() if value is not None and value > 0
    )

    results = []
    for security_id, data in gathered.items():
        results.append(
            score_security(
                data, security_id, score_date, cutoff, cfg, cfg_hash, snapshot_id,
                populations, size_ratios[security_id], positive_s,
                price_dataset_version, price_snapshot_hash,
            )
        )
    return results


def score_security(data: dict, security_id: int, score_date: str, cutoff: str,
                   cfg: dict, cfg_hash: str, snapshot_id: str, populations: dict,
                   size: tuple[float | None, dict], positive_s: list[float],
                   price_dataset_version, price_snapshot_hash: str) -> dict:
    member = data["member"]
    cohort_id = member["cohort_id"]
    row = data["fundamentals"]
    withhold: list[str] = []

    def ranked(metric: str, cohort_blended: bool = True) -> RankedMetric:
        raw = None
        if row is not None and metric in row.keys() and row[metric] is not None:
            raw = float(row[metric])
        return rank_metric(metric, raw, security_id, cohort_id, populations, cfg,
                           cutoff, snapshot_id, cohort_blended)

    # ------------------------------------------------------------------ VALUE
    value_component = WeightedComponent("value", share=1.0)
    for metric, nominal in VALUE_WEIGHTS.items():
        value_component.add(metric, nominal, ranked(metric))
    value_valid = len(value_component.valid_items())
    value_score = None
    if value_valid >= MIN_VALID_VALUE_SUBMETRICS:
        value_score = value_component.score()
    else:
        withhold.append(
            f"Value gate: only {value_valid} of 4 submetrics valid, at least "
            f"{MIN_VALID_VALUE_SUBMETRICS} required"
        )

    # ---------------------------------------------------------------- QUALITY
    piotroski = (
        QUAL.read_piotroski(row, data["prior_period"])
        if row is not None
        else QUAL.Piotroski(False, None, [], None, None,
                            "no fundamentals at the knowledge cutoff")
    )
    quality_component = WeightedComponent("quality", share=QUAL.NON_PIOTROSKI_SHARE)
    for metric, nominal in QUAL.NON_PIOTROSKI_WEIGHTS.items():
        quality_component.add(metric, nominal, ranked(metric))
    quality_valid = len(quality_component.valid_items())
    quality_score = None
    if not piotroski.complete:
        withhold.append(f"Quality gate: {piotroski.reason}")
    elif quality_valid < QUAL.MIN_VALID_NON_PIOTROSKI:
        withhold.append(
            f"Quality gate: only {quality_valid} of 4 non-Piotroski metrics valid, "
            f"at least {QUAL.MIN_VALID_NON_PIOTROSKI} required"
        )
    else:
        quality_component.fixed.append({
            "metric": "piotroski_f_score",
            "weight": QUAL.PIOTROSKI_WEIGHT,
            "value": piotroski.contribution_value,
            "raw_value": piotroski.f_score,
            "detail": piotroski.to_json(),
        })
        quality_score = quality_component.score()

    # --------------------------------------------------------------- MOMENTUM
    momentum = data["momentum"]
    momentum_component = WeightedComponent("momentum", share=1.0, renormalise=False)
    momentum_score = None
    if not momentum.gate_passed:
        withhold.append(f"Momentum gate: {momentum.gate_reason}")
        for metric in MOM.WEIGHTS:
            momentum_component.add(
                metric, MOM.WEIGHTS[metric],
                RankedMetric(metric, momentum.values[metric], False, None,
                             momentum.gate_reason, MARKET_POPULATION, 0, None,
                             None, 0, None, 0.0, cutoff, snapshot_id),
            )
    else:
        for metric in MOM.PERCENTILE_RANKED:
            momentum_component.add(
                metric, MOM.WEIGHTS[metric],
                rank_metric(metric, momentum.values[metric], security_id, cohort_id,
                            populations, cfg, cutoff, snapshot_id, cohort_blended=False),
            )
        for metric in MOM.USED_DIRECTLY:
            momentum_component.fixed.append({
                "metric": metric,
                "weight": MOM.WEIGHTS[metric],
                "value": momentum.values[metric],
                "raw_value": momentum.values[metric],
                "detail": momentum.detail.get(metric),
            })
        # No renormalisation: every percentile-ranked input must be present.
        if len(momentum_component.valid_items()) != len(MOM.PERCENTILE_RANKED):
            missing = [
                key for key, _, item in momentum_component.items if not item.valid
            ]
            withhold.append(
                "Momentum gate: no renormalisation is permitted and these inputs "
                "could not be ranked: " + ", ".join(sorted(missing))
            )
        else:
            momentum_score = momentum_component.score()

    # ---------------------------------------------------------- INSIDER BONUS
    coverage = data["coverage"]
    as_of = parse_date(score_date)
    b1, b1_detail = INS.cluster_bonus(data["purchases"], as_of)
    b2, b2_detail = INS.executive_bonus(data["purchases"], as_of)
    b4, b4_detail = INS.conviction_bonus(data["purchases"], as_of)
    s_value, s_detail = size
    b3 = 0.0
    s_pct = None
    if s_value is not None and s_value > 0:
        s_pct = percentile(s_value, positive_s)
        if s_pct is None:
            s_detail["reason"] = (
                "fewer than two securities have a positive S; the percentile is "
                "unavailable, so B3 contributes nothing"
            )
        else:
            b3 = 2.0 * s_pct / 100.0
    s_detail.update({
        "population": "securities with S > 0 in the official snapshot",
        "population_count": len(positive_s),
        "percentile": s_pct,
        "formula": "B3 = 2 * pct(S, {S > 0}) / 100",
        "value": b3,
    })

    parts = INS.BonusParts(b1, b2, b3, b4, s_value)
    insider_bonus = None
    if coverage.complete:
        insider_bonus = parts.total
    else:
        withhold.append(f"Insider coverage unknown: {coverage.reason}")

    # ------------------------------------------------------- DILUTION PENALTY
    dilution = data["dilution"]
    dilution_penalty = float(dilution["dilution_score"]) if dilution else 0.0
    dilution_detail = {
        "as_of_date": dilution["as_of_date"] if dilution else None,
        "dilution_score": dilution_penalty,
        "is_disqualified": int(dilution["is_disqualified"]) if dilution else 0,
        "shares_yoy_growth": dilution["shares_yoy_growth"] if dilution else None,
        "note": (
            "F7 dilution score, subtracted from the composite. F8 defines the "
            "withhold conditions exhaustively and disqualification is not one of "
            "them, so a disqualified security is still ranked and carries the flag."
        ),
    }
    if dilution is None:
        dilution_detail["note"] = (
            "no dilution signal at or before the score date; penalty is 0 because "
            "absence of evidence is never a penalty (F7 rule)"
        )

    # -------------------------------------------------------------- COMPOSITE
    rankable = not withhold
    composite = None
    composite_detail: dict = {
        "formula": (
            "0.30*Value + 0.30*Quality + 0.30*Momentum + InsiderBonus "
            "- DilutionPenalty, clamped to [0, 100]"
        ),
        "component_weights": COMPONENT_WEIGHTS,
        "renormalisation_across_components": "never",
    }
    if rankable:
        terms = [
            {"term": "0.30 * Value", "weight": 0.30, "component": value_score,
             "contribution": 0.30 * value_score},
            {"term": "0.30 * Quality", "weight": 0.30, "component": quality_score,
             "contribution": 0.30 * quality_score},
            {"term": "0.30 * Momentum", "weight": 0.30, "component": momentum_score,
             "contribution": 0.30 * momentum_score},
            {"term": "+ InsiderBonus", "weight": 1.0, "component": insider_bonus,
             "contribution": insider_bonus},
            {"term": "- DilutionPenalty", "weight": -1.0, "component": dilution_penalty,
             "contribution": -dilution_penalty},
        ]
        unclamped = sum(term["contribution"] for term in terms)
        composite = max(0.0, min(100.0, unclamped))
        composite_detail.update({
            "terms": terms, "unclamped": unclamped, "clamped": composite,
        })

    explanation = {
        "security_id": security_id,
        "symbol": member["symbol"],
        "name": member["name"],
        "score_date": score_date,
        "knowledge_cutoff": cutoff,
        "snapshot_id": snapshot_id,
        "cohort_id": cohort_id,
        "cohort_label": member["cohort_label"],
        "cohort_basis": "SIC-derived. Not GICS; no GICS data exists in this system.",
        "provenance": {
            "strategy_version": int(cfg["strategy_version"]),
            "config_hash": cfg_hash,
            "mapping_version": (
                row["mapping_version"] if row is not None and "mapping_version" in row.keys()
                else None
            ),
            "fundamentals_period_end": (
                row["period_end"] if row is not None else None
            ),
            "fundamentals_knowledge_date": (
                row["knowledge_date"] if row is not None else None
            ),
            "price_dataset_version": price_dataset_version,
            "price_snapshot_hash": price_snapshot_hash,
            "code_version": CODE_VERSION,
        },
        "components": {
            "value": {
                "weight": 0.30, "score": value_score,
                "gate": f"at least {MIN_VALID_VALUE_SUBMETRICS} of 4 submetrics valid",
                "valid_submetrics": value_valid,
                "detail": value_component.to_json(),
            },
            "quality": {
                "weight": 0.30, "score": quality_score,
                "gate": (
                    "Piotroski fully computable from two consecutive complete fiscal "
                    f"years, plus at least {QUAL.MIN_VALID_NON_PIOTROSKI} of the "
                    "remaining 4"
                ),
                "valid_non_piotroski": quality_valid,
                "piotroski": piotroski.to_json(),
                "detail": quality_component.to_json(),
            },
            "momentum": {
                "weight": 0.30, "score": momentum_score,
                "gate": f"at least {MOM.MIN_TRADING_DAYS} adjusted trading days",
                "population": "whole operating universe, never the cohort",
                "bar_count": momentum.bar_count,
                "inputs": momentum.detail,
                "detail": momentum_component.to_json(),
            },
        },
        "insider_bonus": {
            "value": insider_bonus,
            "formula": "min(10, B1 + B2 + B3 + B4)",
            "coverage": {
                "complete": coverage.complete,
                "reason": coverage.reason,
                "detail": coverage.detail,
                "note": (
                    "Complete coverage with no qualifying purchase is an OBSERVED "
                    "ZERO and is ranked. Incomplete or stale coverage is UNKNOWN "
                    "and withholds ranking."
                ),
            },
            "credibility_table": INS.PLAN_CREDIBILITY,
            "decay": "d(a) = max(0, 1 - a / 180)",
            "qualifying_definition": "Table I, transaction code P, not superseded",
            "qualifying_purchases": len(data["purchases"]),
            "b1_cluster": b1_detail,
            "b2_executive": b2_detail,
            "b3_size": s_detail,
            "b4_conviction": b4_detail,
            "sum_before_cap": b1 + b2 + b3 + b4,
        },
        "dilution_penalty": dilution_detail,
        "composite": composite_detail,
        "rankable": rankable,
        "withhold_reason": None if rankable else "; ".join(withhold),
        "altman_z_note": (
            "Altman Z'' is deliberately absent from the composite. It is a risk "
            "flag only and belongs to F9."
        ),
        "winsorisation_note": (
            "The interest coverage cap (50) and current ratio cap (5.0) are applied "
            "in F5 where the raw magnitudes enter arithmetic, and are not reapplied "
            "here. Percentiles are order statistics, so winsorising before ranking "
            "would change nothing and no such code exists."
        ),
    }

    return {
        "security_id": security_id,
        "symbol": member["symbol"],
        "score_date": score_date,
        "strategy_version": int(cfg["strategy_version"]),
        "config_hash": cfg_hash,
        "mapping_version": str(
            row["mapping_version"] if row is not None and "mapping_version" in row.keys()
            else cfg["mapping_version"]
        ),
        "price_dataset_version": price_dataset_version,
        "price_snapshot_hash": price_snapshot_hash,
        "value_score": value_score,
        "quality_score": quality_score,
        "momentum_score": momentum_score,
        "insider_bonus": insider_bonus,
        "dilution_penalty": dilution_penalty,
        "composite_score": composite,
        "rank": None,
        "cohort_id": cohort_id,
        "rankable": 1 if rankable else 0,
        "withhold_reason": None if rankable else "; ".join(withhold),
        "explanation_json": json.dumps(explanation),
    }


def assign_ranks(results: list[dict]) -> None:
    """Competition ranking: ties share a rank and consume the positions after it."""
    ranked = sorted(
        [r for r in results if r["rankable"] == 1],
        key=lambda r: (-r["composite_score"], r["security_id"]),
    )
    previous_score = None
    previous_rank = 0
    for index, row in enumerate(ranked, start=1):
        if previous_score is not None and abs(row["composite_score"] - previous_score) < 1e-12:
            row["rank"] = previous_rank
        else:
            row["rank"] = index
            previous_rank = index
        previous_score = row["composite_score"]


# ------------------------------------------------------------------ excluded


def excluded_row(member: dict, score_date: str, cfg: dict, cfg_hash: str,
                 snapshot_id: str) -> dict:
    explanation = {
        "security_id": int(member["security_id"]),
        "symbol": member["symbol"],
        "name": member["name"],
        "score_date": score_date,
        "snapshot_id": snapshot_id,
        "cohort_id": member["cohort_id"],
        "cohort_label": member["cohort_label"],
        "cohort_basis": "SIC-derived. Not GICS; no GICS data exists in this system.",
        "rankable": False,
        "withhold_reason": member["exclusion_reason"],
        "universe_status": "excluded",
        "security_type": member["security_type"],
        "sic_code": member["sic_code"],
    }
    return {
        "security_id": int(member["security_id"]),
        "symbol": member["symbol"],
        "score_date": score_date,
        "strategy_version": int(cfg["strategy_version"]),
        "config_hash": cfg_hash,
        "mapping_version": str(cfg["mapping_version"]),
        "price_dataset_version": None,
        "price_snapshot_hash": None,
        "value_score": None, "quality_score": None, "momentum_score": None,
        "insider_bonus": None, "dilution_penalty": 0.0,
        "composite_score": None, "rank": None,
        "cohort_id": member["cohort_id"],
        "rankable": 0,
        "withhold_reason": member["exclusion_reason"],
        "explanation_json": json.dumps(explanation),
    }


# ---------------------------------------------------------------------- main


def write_scores(conn: sqlite3.Connection, rows: list[dict]) -> int:
    columns = [
        "security_id", "score_date", "strategy_version", "config_hash",
        "mapping_version", "price_dataset_version", "price_snapshot_hash",
        "value_score", "quality_score", "momentum_score", "insider_bonus",
        "dilution_penalty", "composite_score", "rank", "cohort_id", "rankable",
        "withhold_reason", "explanation_json",
    ]
    quoted = ",".join(f'"{c}"' for c in columns)
    for row in rows:
        conn.execute(
            f"INSERT OR REPLACE INTO scores ({quoted}) "
            f"VALUES ({','.join('?' * len(columns))})",
            [row[c] for c in columns],
        )
    return len(rows)


def report(rows: list[dict]) -> str:
    ranked = sorted(
        [r for r in rows if r["rankable"] == 1], key=lambda r: (r["rank"], r["symbol"])
    )
    withheld = sorted(
        [r for r in rows if r["rankable"] == 0], key=lambda r: r["symbol"]
    )
    lines = [
        "",
        f"RANKED  ({len(ranked)} securities)",
        f"{'#':>3}  {'SYM':<7}{'VALUE':>8}{'QUALITY':>9}{'MOMENT':>8}"
        f"{'INSIDER':>9}{'DILUTE':>8}{'COMPOSITE':>11}  COHORT",
    ]
    for row in ranked:
        lines.append(
            f"{row['rank']:>3}  {row['symbol']:<7}{row['value_score']:>8.2f}"
            f"{row['quality_score']:>9.2f}{row['momentum_score']:>8.2f}"
            f"{row['insider_bonus']:>9.2f}{row['dilution_penalty']:>8.2f}"
            f"{row['composite_score']:>11.4f}  {row['cohort_id']}"
        )
    lines += ["", f"UNRANKABLE  ({len(withheld)} securities)"]
    for row in withheld:
        lines.append(f"  {row['symbol']:<7} {row['withhold_reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute composite scores")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument(
        "--pool", action="append", default=None,
        help="score a universe_candidate_pool version instead of the Phase F fixture; "
        "repeatable to combine pools",
    )
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    cfg_hash = config_hash(args.config)
    conn = migrate.connect(Path(args.db))
    run_id = f"scoring-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        score_date = resolve_score_date(conn, args.as_of)
        if score_date is None:
            print("no price bars at or before the as-of date; nothing to score")
            conn.execute("ROLLBACK")
            return 1
        cutoff = f"{score_date}T23:59:59Z"

        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'scoring', ?, 'running', ?)",
            (run_id, utc_now(), CODE_VERSION),
        )
        snapshot_id, members = ensure_snapshot(conn, score_date, cutoff, cfg_hash, run_id, args.pool)
        results = score_universe(conn, score_date, cfg, cfg_hash, snapshot_id, members, cutoff)
        results += [
            excluded_row(m, score_date, cfg, cfg_hash, snapshot_id)
            for m in members if m["status"] != "included"
        ]
        assign_ranks(results)
        written = write_scores(conn, results)

        conn.execute(
            "UPDATE pipeline_runs SET status='success', finished_at=?, records_written=? "
            "WHERE run_id=?",
            (utc_now(), written, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print(f"score date {score_date}   snapshot {snapshot_id}   config {cfg_hash[:12]}")
    print(report(results))
    print(f"\nscores written: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
