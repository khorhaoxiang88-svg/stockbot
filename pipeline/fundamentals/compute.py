"""Compute derived fundamentals from stored XBRL facts.

Per security, per fiscal period, per KNOWLEDGE STATE.

A knowledge state is one acceptance timestamp at which some filing reported that
period. The original 10-K is one state; an amendment months later is another.
Each produces its own row, so "what did we know on date X" is answerable by
filtering knowledge_date <= X. Rows are never overwritten.

Inputs are resolved through the versioned concept mapping, in priority order,
using only facts accepted on or before the knowledge date. Whatever concept wins
is recorded on the row alongside the accession it came from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import load_config  # noqa: E402
from fundamentals import metrics as M  # noqa: E402
from fundamentals.mappings import (  # noqa: E402
    CONCEPT_MAP,
    DURATION_INPUTS,
    MAPPING_VERSION,
    load_concept_mappings,
    seed_concept_mappings,
)

ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 330, 400
MAX_KNOWLEDGE_STATES = 6
EARLIEST_PERIOD = "2022-06-30"
MAX_PERIODS = 3

# Order matters: this is the column order used when writing rows.
SCALAR_METRICS = [
    "pe", "pb", "ev_ebitda", "fcf_yield", "roic", "interest_coverage",
    "debt_ebitda", "current_ratio", "gross_margin", "revenue_growth_yoy",
    "shares_outstanding",
]
PIOTROSKI_METRICS = [
    "piotroski_roa_positive", "piotroski_cfo_positive", "piotroski_roa_improved",
    "piotroski_accruals", "piotroski_leverage_decreased",
    "piotroski_current_ratio_improved", "piotroski_no_new_shares",
    "piotroski_gross_margin_improved", "piotroski_asset_turnover_improved",
]
ALL_METRICS = SCALAR_METRICS + PIOTROSKI_METRICS


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days(start: str, end: str) -> int:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days


class FactIndex:
    """All mapped facts for one company, indexed for point-in-time resolution."""

    def __init__(self, conn, cik: str, concepts: set[tuple[str, str]]):
        self.rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
        placeholders = ",".join("?" for _ in concepts)
        params = [cik] + [concept for _, concept in concepts]
        for row in conn.execute(
            f"""
            SELECT taxonomy, concept, period_start, period_end, normalized_numeric_value,
                   accession_no, accepted_at, form_type
              FROM xbrl_facts
             WHERE cik = ? AND concept IN ({placeholders})
               AND accepted_at IS NOT NULL AND normalized_numeric_value IS NOT NULL
            """,
            params,
        ):
            self.rows[(row["taxonomy"], row["concept"])].append(dict(row))

    def resolve(
        self, input_name: str, candidates: list[tuple[str, str]], period_end: str,
        knowledge_date: str,
    ) -> M.Input:
        """First candidate concept with a usable fact, honouring the cutoff."""
        want_duration = input_name in DURATION_INPUTS
        for taxonomy, concept in candidates:
            best = None
            for row in self.rows.get((taxonomy, concept), ()):
                if row["period_end"] != period_end:
                    continue
                if row["accepted_at"] > knowledge_date:
                    continue  # not knowable yet
                if want_duration:
                    if not row["period_start"]:
                        continue
                    span = _days(row["period_start"], row["period_end"])
                    if not (ANNUAL_MIN_DAYS <= span <= ANNUAL_MAX_DAYS):
                        continue
                elif row["period_start"]:
                    continue
                if best is None or row["accepted_at"] > best["accepted_at"]:
                    best = row
            if best is not None:
                return M.Input(
                    float(best["normalized_numeric_value"]), f"{taxonomy}:{concept}",
                    best["accession_no"],
                )
        return M.MISSING

    def resolve_instant_asof(
        self, candidates: list[tuple[str, str]], as_of: str, knowledge_date: str
    ) -> M.Input:
        """Latest instant fact dated on or before `as_of`, knowable at the cutoff.

        Share counts need this. dei:EntityCommonStockSharesOutstanding is a
        cover-page instant dated when the filing was prepared, not at the fiscal
        period end, so exact period matching almost never finds it. Market cap is
        specified as point-in-time, so the right value is the most recent share
        count we could have known, not one pinned to the accounting period.

        A non-positive share count is never valid evidence -- confirmed against
        real ingested data during S1: HVT.A's own filing history tags
        dei:EntityCommonStockSharesOutstanding as literally 0 in one 2012
        accession (0000216085-12-000014), a filer error that SEC's data
        preserves verbatim. Treating it as "present" propagated a $0 market
        cap, and from there a $0-numerator P/E, for every later knowledge date
        that had no closer share count -- the exact "zero for absent data"
        F12 check 9 exists to catch. Skipped here, per-row, so the search
        keeps looking for the next most recent genuinely positive instant
        instead of either accepting the bad value or discarding the whole
        concept.
        """
        best = None
        for taxonomy, concept in candidates:
            for row in self.rows.get((taxonomy, concept), ()):
                if row["period_start"]:
                    continue  # instants only
                if row["period_end"] > as_of or row["accepted_at"] > knowledge_date:
                    continue
                if row["normalized_numeric_value"] is None or row["normalized_numeric_value"] <= 0:
                    continue  # never valid: a real company cannot have <= 0 shares outstanding
                if best is None or (row["period_end"], row["accepted_at"]) > (
                    best["period_end"], best["accepted_at"]
                ):
                    best = (row, taxonomy, concept)[0]
                    best_tag = f"{taxonomy}:{concept}"
            if best is not None:
                return M.Input(float(best["normalized_numeric_value"]), best_tag,
                               best["accession_no"])
        return M.MISSING

    def _all_annual_ends(self) -> set[str]:
        """Every fiscal year end, identified by an annual-duration 10-K fact.

        This must not be built from raw period_end values: cover-page instants
        such as dei:EntityCommonStockSharesOutstanding carry their own dates
        (AAPL reports one at 2024-10-18), and treating those as fiscal year ends
        makes prior-year comparisons resolve against a date with no income
        statement, silently nulling every year-over-year metric.
        """
        ends: set[str] = set()
        for rows in self.rows.values():
            for row in rows:
                if not row["period_start"] or not row["form_type"]:
                    continue
                if not row["form_type"].startswith("10-K"):
                    continue
                if ANNUAL_MIN_DAYS <= _days(row["period_start"], row["period_end"]) <= ANNUAL_MAX_DAYS:
                    ends.add(row["period_end"])
        return ends

    def annual_period_ends(self, since: str = EARLIEST_PERIOD) -> list[str]:
        return sorted(e for e in self._all_annual_ends() if e >= since)

    def knowledge_dates(self, period_end: str) -> list[str]:
        stamps = {
            row["accepted_at"]
            for rows in self.rows.values()
            for row in rows
            if row["period_end"] == period_end and row["accepted_at"]
        }
        return sorted(stamps)[-MAX_KNOWLEDGE_STATES:]

    def prior_period_end(self, period_end: str) -> str | None:
        """The FISCAL YEAR END roughly 12 months earlier, if we have one.

        Chosen only from annual 10-K period ends, never from arbitrary fact
        dates, for the reason given in _all_annual_ends.
        """
        best = None
        for candidate in self._all_annual_ends():
            if candidate >= period_end:
                continue
            if 300 <= _days(candidate, period_end) <= 430 and (best is None or candidate > best):
                best = candidate
        return best


def _total(*inputs: M.Input) -> M.Input:
    """Sum of the present components; missing when none are present."""
    present = [i for i in inputs if i.present]
    if not present:
        return M.MISSING
    return M.Input(sum(i.value for i in present), present[0].concept, present[0].accession)


def _price_before(conn, security_id: int, knowledge_date: str) -> tuple[float | None, str | None]:
    row = conn.execute(
        "SELECT date, close FROM prices WHERE security_id = ? AND date <= ? "
        "AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
        (security_id, knowledge_date[:10]),
    ).fetchone()
    if not row:
        return None, None
    return float(row["close"]), row["date"]


def compute_row(conn, security: dict, index: FactIndex, mapping: dict, period_end: str,
                knowledge_date: str, config) -> dict | None:
    """One derived_fundamentals row, or None when nothing could be resolved."""
    resolve = lambda name: index.resolve(name, mapping.get(name, []), period_end, knowledge_date)  # noqa: E731
    prior_end = index.prior_period_end(period_end)
    resolve_prior = (
        lambda name: index.resolve(name, mapping.get(name, []), prior_end, knowledge_date)
        if prior_end else M.MISSING
    )  # noqa: E731

    revenue = resolve("revenue")
    cost_of_revenue = resolve("cost_of_revenue")
    gross_profit = resolve("gross_profit")
    net_income = resolve("net_income")
    eps = resolve("eps_diluted")
    equity = resolve("stockholders_equity")
    assets = resolve("assets")
    current_assets = resolve("current_assets")
    current_liabilities = resolve("current_liabilities")
    cash = resolve("cash")
    short_debt = resolve("short_term_debt")
    long_debt = resolve("long_term_debt")
    operating_income = resolve("operating_income")
    depreciation = resolve("depreciation_amortization")
    interest = resolve("interest_expense")
    cfo = resolve("cfo")
    capex = resolve("capex")
    # Point-in-time share count: the latest one knowable at this knowledge date.
    shares = index.resolve_instant_asof(
        mapping.get("shares_outstanding", []), knowledge_date[:10], knowledge_date
    )
    if not shares.present:
        shares = resolve("shares_outstanding")
    # For the year-over-year share comparison, anchor each side to its own
    # fiscal period end instead of to today.
    shares_at_period = index.resolve_instant_asof(
        mapping.get("shares_outstanding", []), period_end, knowledge_date
    )
    income_tax = resolve("income_tax")
    pretax = resolve("pretax_income")

    total_debt = _total(short_debt, long_debt)

    if not any(i.present for i in (revenue, net_income, assets, equity)):
        return None  # nothing usable at this knowledge date

    # ---- market cap, point in time
    price, price_date = _price_before(conn, security["security_id"], knowledge_date)
    market_cap = M.MISSING
    mc_confidence = mc_reason = None
    if shares.present and price is not None:
        market_cap = M.Input(shares.value * price, shares.concept, shares.accession)
        if security["class_count"] > 1:
            mc_confidence = "low"
            mc_reason = (
                f"multi-class issuer: {security['class_count']} listed classes share CIK "
                f"{security['cik']}; this uses one class's share count and price only"
            )
        elif shares.concept and shares.concept.startswith("dei:"):
            mc_confidence = "high"
        else:
            mc_confidence = "medium"
            mc_reason = f"share count from {shares.concept}, not the cover-page tag"

    cap_ic = float(config["interest_coverage_cap"])
    cap_cr = float(config["current_ratio_cap"])

    ebitda = M.ebitda(operating_income, depreciation)
    ev = M.enterprise_value(market_cap, total_debt, cash)
    current_ratio = M.current_ratio(current_assets, current_liabilities, cap_cr)
    gross_margin = M.gross_margin(gross_profit, revenue, cost_of_revenue)

    prior_current_ratio = M.current_ratio(
        resolve_prior("current_assets"), resolve_prior("current_liabilities"), cap_cr
    )
    prior_gross_margin = M.gross_margin(
        resolve_prior("gross_profit"), resolve_prior("revenue"), resolve_prior("cost_of_revenue")
    )

    computed: dict[str, M.Metric] = {
        "pe": M.price_earnings(market_cap, eps, M.Input(price), net_income),
        "pb": M.price_book(market_cap, equity),
        "ev_ebitda": M.ev_to_ebitda(ev, ebitda),
        "fcf_yield": M.fcf_yield(cfo, capex, market_cap),
        "roic": M.roic(operating_income, income_tax, pretax, total_debt, equity, cash),
        "interest_coverage": M.interest_coverage(operating_income, interest, total_debt, cap_ic),
        "debt_ebitda": M.debt_to_ebitda(total_debt, ebitda),
        "current_ratio": current_ratio,
        "gross_margin": gross_margin,
        "revenue_growth_yoy": M.revenue_growth(revenue, resolve_prior("revenue")),
        "shares_outstanding": (
            M.Metric(shares.value, shares.concept, shares.accession)
            if shares.present else M.Metric.unavailable("missing:shares_outstanding")
        ),
        "piotroski_roa_positive": M.piotroski_roa_positive(net_income, assets),
        "piotroski_cfo_positive": M.piotroski_cfo_positive(cfo),
        "piotroski_roa_improved": M.piotroski_roa_improved(
            net_income, assets, resolve_prior("net_income"), resolve_prior("assets")),
        "piotroski_accruals": M.piotroski_accruals(cfo, net_income, assets),
        "piotroski_leverage_decreased": M.piotroski_leverage_decreased(
            long_debt, assets, resolve_prior("long_term_debt"), resolve_prior("assets")),
        "piotroski_current_ratio_improved": M.piotroski_current_ratio_improved(
            current_ratio, prior_current_ratio, current_assets),
        "piotroski_no_new_shares": M.piotroski_no_new_shares(
            shares_at_period if shares_at_period.present else shares,
            index.resolve_instant_asof(
                mapping.get("shares_outstanding", []), prior_end, knowledge_date
            ) if prior_end else M.MISSING,
        ),
        "piotroski_gross_margin_improved": M.piotroski_gross_margin_improved(
            gross_margin, prior_gross_margin, revenue),
        "piotroski_asset_turnover_improved": M.piotroski_asset_turnover_improved(
            revenue, assets, resolve_prior("revenue"), resolve_prior("assets")),
    }

    missing = {name: result.reason for name, result in computed.items() if result.value is None}
    applicable, applicability_reason = M.model_applicable(security["sic_code"])
    if not applicable:
        missing["_model_applicable"] = applicability_reason

    used = sorted(
        (name, result.concept_used or "", result.accession or "", repr(result.value))
        for name, result in computed.items()
    )
    fact_set_hash = hashlib.sha256(
        json.dumps(used, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    row = {
        "security_id": security["security_id"],
        "period_end": period_end,
        "knowledge_date": knowledge_date,
        "fact_set_hash": fact_set_hash,
        "mapping_version": MAPPING_VERSION,
        "market_cap": market_cap.value,
        "market_cap_confidence": mc_confidence,
        "market_cap_shares_used": shares.value,
        "market_cap_price_used": price,
        "market_cap_price_date": price_date,
        "market_cap_concept_used": shares.concept,
        "market_cap_accession": shares.accession,
        "market_cap_ambiguity_reason": mc_reason,
        "inputs_complete": 1 if not [k for k in missing if not k.startswith("_")] else 0,
        "missing_fields_json": json.dumps(missing, sort_keys=True) if missing else None,
        "model_applicable": 1 if applicable else 0,
        "computed_at": utc_now(),
    }
    for name, result in computed.items():
        row[name] = result.value
        row[f"{name}_concept_used"] = result.concept_used
        row[f"{name}_accession"] = result.accession
    return row


def write_row(conn, row: dict) -> None:
    columns = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO derived_fundamentals ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        [row[c] for c in columns],
    )


def fixture_securities(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.security_id, s.cik, s.sic_code, f.symbol_at_selection AS symbol,
               (SELECT COUNT(*) FROM securities s2 WHERE s2.cik = s.cik) AS class_count
          FROM fixture_manifest f
          JOIN securities s ON s.security_id = f.security_id
         WHERE s.cik IS NOT NULL
         ORDER BY s.security_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute derived fundamentals")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--since", default=EARLIEST_PERIOD,
                        help="earliest fiscal period end to compute")
    parser.add_argument("--max-periods", type=int, default=MAX_PERIODS)
    parser.add_argument(
        "--pool",
        default=None,
        help="compute for a universe_candidate_pool version instead of the Phase F fixture "
        "(e.g. s1-sample-v1); does not touch fixture_manifest",
    )
    args = parser.parse_args(argv)

    config = load_config()
    conn = migrate.connect(Path(args.db))
    run_id = f"fundamentals-{uuid.uuid4().hex[:12]}"
    all_concepts = {(t, c) for candidates in CONCEPT_MAP.values() for t, c, _, _ in candidates}

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'fundamentals', ?, 'running', ?)",
            (run_id, utc_now(), MAPPING_VERSION),
        )
        seeded = seed_concept_mappings(conn)
        mapping = load_concept_mappings(conn)

        if args.pool:
            from universe.pool import pool_securities

            securities = pool_securities(conn, args.pool)
        else:
            securities = fixture_securities(conn)
        if args.symbols:
            wanted = {s.upper() for s in args.symbols}
            securities = [s for s in securities if s["symbol"].upper() in wanted]

        written = 0
        for security in securities:
            index = FactIndex(conn, security["cik"], all_concepts)
            periods = [p for p in index.annual_period_ends(args.since)][-args.max_periods:]
            rows_here = 0
            for period_end in periods:
                for knowledge_date in index.knowledge_dates(period_end):
                    row = compute_row(
                        conn, security, index, mapping, period_end, knowledge_date, config
                    )
                    if row:
                        write_row(conn, row)
                        rows_here += 1
            written += rows_here
            print(f"{security['symbol']:<8} periods={len(periods)} rows={rows_here}")

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

    print(f"\nconcept mappings seeded: {seeded}")
    print(f"derived rows written   : {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
