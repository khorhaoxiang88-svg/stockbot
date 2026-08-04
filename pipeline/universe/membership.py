"""S1 rules-based universe: entry/retention rules and hysteresis.

Two run types, two different jobs:

  * monthly_membership -- the only run type allowed to change status between
    'included' and 'excluded'. A not-yet-included security must clear the
    (higher) ENTRY thresholds to become 'included'. An already-included
    security is re-checked against the (lower) RETENTION thresholds; it only
    formally exits once its consecutive-trading-days-below-retention counter
    reaches the configured hysteresis window. Every entry/exit is logged to
    universe_membership_changes.

  * daily_safety -- never changes formal membership. It can only mark an
    already-included security 'watch' for immediate safety reasons (stale
    price, no recent bar, severe new dilution evidence) that must not wait
    for the next monthly cycle. It also advances the hysteresis counter one
    trading day at a time -- the counter is what makes "N consecutive trading
    days below retention" meaningful; the monthly run only reads it.

Why this prevents oscillation: once a security is 'included', every later
check (daily or monthly) tests it against RETENTION, never ENTRY, again.
Entry is a one-time higher bar to get in; a security that dips just below
entry but stays above retention never sees the entry bar again, so it cannot
flap in and out around that line. It can only exit by failing the LOWER bar
for the full hysteresis window, and monthly cadence means that exit is never
faster than the brief's "formal changes are applied monthly" rule.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from config_loader import load_config  # noqa: E402
from execution.compute import dollar_adv  # noqa: E402
from scoring.compute import config_hash  # noqa: E402
from universe.classify import RANKABLE_CONFIDENCES  # noqa: E402

# NYSE, Nasdaq and NYSE American only. otherlisted.txt also carries NYSE Arca,
# Cboe BZX and IEX rows, which are out of scope for the brief's entry rule.
# Single source of truth: pool_loader.py imports this rather than redefining it.
ALLOWED_EXCHANGES = {"NYSE", "Nasdaq", "NYSE American"}

ADV_WINDOW_DAYS = 60

# security_type values the brief excludes outright, with the human-readable
# label used in the exclusion reason. Order does not matter here; classify.py
# already resolved exactly one type per security.
EXCLUDED_TYPE_LABELS = {
    "etf": "ETF",
    "etn": "ETN",
    "closed_end_fund": "closed-end fund",
    "trust_unit": "royalty or unit trust",
    "warrant": "warrant",
    "right": "right",
    "unit": "unit (includes pre-merger SPAC units)",
    "preferred_share": "preferred share",
    "test_issue": "test issue",
    "adr": "ADR (excluded in Release 1)",
    "unknown": "classification unresolved",
}

# Known, documented gap: a pre-merger SPAC that already trades as plain common
# stock (post-IPO, pre-business-combination, no "Units" ticker suffix) is not
# distinguishable from a genuine operating company by classify.py alone --
# there is no 'spac' security_type, and nothing here invents one. Only the
# UNIT form of a pre-merger SPAC is caught, via the 'unit' exclusion above.
# This is implemented, not worked around: it is stated here rather than
# silently claiming full SPAC coverage.


class MembershipError(RuntimeError):
    pass


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- data gathering


def current_listing(conn, security_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT symbol, exchange FROM listings WHERE security_id = ? AND valid_to IS NULL "
        "ORDER BY is_primary DESC LIMIT 1",
        (security_id,),
    ).fetchone()
    return dict(row) if row else None


def latest_price(conn, security_id: int, as_of: str) -> tuple[float | None, str | None]:
    """(close, date) of the most recent raw bar at or before as_of."""
    row = conn.execute(
        "SELECT close, date FROM prices WHERE security_id = ? AND date <= ? "
        "AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
        (security_id, as_of),
    ).fetchone()
    if not row:
        return None, None
    return float(row["close"]), row["date"]


def price_data_gap_trading_days(conn, security_id: int, as_of: str) -> int | None:
    """How many of the most recent known trading sessions have no bar for this
    security, counting back from as_of. None when there is no price data at
    all (handled as its own exclusion reason by the caller)."""
    sessions = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT date FROM prices WHERE date <= ? ORDER BY date DESC LIMIT 10",
            (as_of,),
        )
    ]
    if not sessions:
        return None
    _, last_price_date = latest_price(conn, security_id, as_of)
    if last_price_date is None:
        return None
    missed = [s for s in sessions if s > last_price_date]
    return len(missed)


def market_cap(conn, security_id: int, as_of: str) -> tuple[float | None, str | None]:
    """Most recently knowable point-in-time market cap and its confidence.

    knowledge_date is a full UTC timestamp; as_of is a bare calendar date.
    Comparing the two strings directly is wrong -- "2026-08-01" sorts BELOW
    "2026-08-01T00:00:00Z" lexicographically, since the shorter string is a
    strict prefix of the longer one, so every same-day knowledge_date would
    be excluded. as_of is widened to the end of its day first, the same
    pattern riskflags/compute.py already uses for this exact comparison.
    """
    cutoff = f"{as_of}T23:59:59Z"
    row = conn.execute(
        "SELECT market_cap, market_cap_confidence FROM derived_fundamentals "
        "WHERE security_id = ? AND knowledge_date <= ? AND market_cap IS NOT NULL "
        "ORDER BY knowledge_date DESC, period_end DESC LIMIT 1",
        (security_id, cutoff),
    ).fetchone()
    if not row:
        return None, None
    return float(row["market_cap"]), row["market_cap_confidence"]


def files_10k_10q(conn, cik: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM filings WHERE cik = ? AND form_type IN ('10-K', '10-Q') LIMIT 1",
        (cik,),
    ).fetchone()
    return row is not None


def _duration_days(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        from datetime import datetime

        return (datetime.fromisoformat(end[:10]) - datetime.fromisoformat(start[:10])).days
    except ValueError:
        return None


def _is_quarter_like(start: str | None, end: str | None) -> bool:
    """A duration fact roughly 75-100 days long -- one explicitly tagged
    fiscal quarter (Q1-Q3, from a 10-Q)."""
    days = _duration_days(start, end)
    return days is not None and 75 <= days <= 100


def _is_annual_like(start: str | None, end: str | None) -> bool:
    """A duration fact roughly 350-380 days long -- a fiscal year, from a
    10-K."""
    days = _duration_days(start, end)
    return days is not None and 350 <= days <= 380


def xbrl_consecutive_quarters(conn, cik: str, as_of: str, max_gap_days: int = 100) -> int:
    """Longest run of consecutive fiscal quarters with usable XBRL data,
    ending at the most recent quarter on or before as_of.

    Distinct (period_start, period_end) pairs across ALL concepts are used
    rather than picking one concept, because issuers map the same quantity to
    different tags (F5's whole reason for concept_mappings) and requiring one
    fixed concept to be present every quarter would undercount real coverage.

    Q4 is never a standalone checkpoint on purpose. SEC filers tag Q1-Q3 as
    explicit ~90-day duration facts on their 10-Qs, but a 10-K reports the
    FULL fiscal year, not a discrete Q4-only duration -- there is usually no
    single tagged fact anywhere that is just "the fourth quarter". Requiring
    one produces a false ~6-month gap every single fiscal year, for every
    filer that follows this completely normal pattern -- confirmed against
    real ingested facts for RCUS and FERG during S1's real-sample run, both
    of which undercounted to single digits despite years of continuous
    filing. The annual fact's period_end IS the Q4 checkpoint: reporting the
    full year is proof the fourth quarter was covered, even though no fact
    is tagged at exactly that quarter's duration. Annual period_ends are
    therefore merged into the checkpoint list alongside the explicit
    quarterly ones, which closes the gap using the real reporting pattern
    instead of loosening the gap tolerance and accepting an actual missed
    quarter as if it were covered.
    """
    rows = conn.execute(
        "SELECT DISTINCT period_start, period_end FROM usable_facts "
        "WHERE cik = ? AND period_end <= ? AND period_start IS NOT NULL",
        (cik, as_of),
    ).fetchall()
    checkpoints = sorted(
        {
            r["period_end"]
            for r in rows
            if _is_quarter_like(r["period_start"], r["period_end"])
            or _is_annual_like(r["period_start"], r["period_end"])
        },
        reverse=True,
    )
    if not checkpoints:
        return 0

    from datetime import datetime

    streak = 1
    for i in range(1, len(checkpoints)):
        gap = (
            datetime.fromisoformat(checkpoints[i - 1][:10])
            - datetime.fromisoformat(checkpoints[i][:10])
        ).days
        if gap > max_gap_days:
            break
        streak += 1
    return streak


def severe_dilution_evidence(conn, security_id: int, as_of: str) -> bool:
    row = conn.execute(
        "SELECT is_disqualified FROM dilution_signals WHERE security_id = ? AND as_of_date <= ? "
        "ORDER BY as_of_date DESC LIMIT 1",
        (security_id, as_of),
    ).fetchone()
    return bool(row and row["is_disqualified"])


def gather_metrics(conn, security: dict, as_of: str, config: dict) -> dict[str, Any]:
    """Everything an entry/retention decision needs for one security."""
    listing = current_listing(conn, security["security_id"])
    price, price_date = latest_price(conn, security["security_id"], as_of)
    adv = dollar_adv(conn, security["security_id"], as_of, window=ADV_WINDOW_DAYS)
    cap, cap_confidence = market_cap(conn, security["security_id"], as_of)
    gap_days = price_data_gap_trading_days(conn, security["security_id"], as_of)
    quarters = xbrl_consecutive_quarters(conn, security["cik"], as_of) if security["cik"] else 0
    files_reports = files_10k_10q(conn, security["cik"]) if security["cik"] else False
    dilution_severe = severe_dilution_evidence(conn, security["security_id"], as_of)

    return {
        "security_id": security["security_id"],
        "cik": security["cik"],
        "security_type": security["security_type"],
        "classification_confidence": security["classification_confidence"],
        "exchange": listing["exchange"] if listing else None,
        "symbol": listing["symbol"] if listing else None,
        "price": price,
        "price_date": price_date,
        "adv_dollar": adv,
        "market_cap": cap,
        "market_cap_confidence": cap_confidence,
        "price_data_gap_days": gap_days,
        "xbrl_consecutive_quarters": quarters,
        "files_10k_10q": files_reports,
        "dilution_severe": dilution_severe,
    }


# ------------------------------------------------------------------ decisions


def entry_exclusion_reason(m: dict[str, Any], config: dict) -> str | None:
    """None means the security clears every entry rule. Checked in a fixed
    order so the reported reason is the first real cause, not whichever
    happened to be checked last."""
    if m["exchange"] not in ALLOWED_EXCHANGES:
        return f"not listed on NYSE, Nasdaq or NYSE American (exchange={m['exchange']!r})"

    if m["security_type"] != "common_stock":
        label = EXCLUDED_TYPE_LABELS.get(m["security_type"], m["security_type"])
        return f"security_type is {label}, not common stock"

    if m["classification_confidence"] not in RANKABLE_CONFIDENCES:
        return (
            f"classification confidence {m['classification_confidence']!r} is below "
            f"the required {config['universe_classification_confidence_min']!r}"
        )

    if not m["cik"]:
        return "no CIK on record"

    if not m["files_10k_10q"]:
        return "no 10-K or 10-Q on file"

    if m["price_data_gap_days"] is None:
        return "no price data on record"
    if m["price_data_gap_days"] >= config["universe_price_data_max_gap_days"]:
        return (
            f"no price data for {m['price_data_gap_days']} of the last "
            f"{config['universe_price_data_max_gap_days']} trading sessions"
        )

    if m["price"] is None or m["price"] < config["universe_entry_price_min"]:
        return f"price {m['price']!r} is below the ${config['universe_entry_price_min']:.2f} entry minimum"

    if m["market_cap"] is None or m["market_cap"] < config["universe_entry_market_cap_min"]:
        return (
            f"market cap {m['market_cap']!r} is below the "
            f"${config['universe_entry_market_cap_min']:,.0f} entry minimum"
        )

    if m["adv_dollar"] is None or m["adv_dollar"] < config["universe_entry_adv_min"]:
        return (
            f"60-day ADV {m['adv_dollar']!r} is below the "
            f"${config['universe_entry_adv_min']:,.0f} entry minimum"
        )

    if m["xbrl_consecutive_quarters"] < config["universe_entry_xbrl_quarters_min"]:
        return (
            f"only {m['xbrl_consecutive_quarters']} consecutive quarter(s) of XBRL data, "
            f"below the {config['universe_entry_xbrl_quarters_min']}-quarter entry minimum"
        )

    return None


def below_retention(m: dict[str, Any], config: dict) -> bool:
    """True when the security has dropped below ANY retention threshold.
    Retention does not re-check exchange, type, confidence, CIK, filing
    status, XBRL depth or price-data gaps -- those are entry-time gates: a
    security already inside the universe is not re-examined against them
    every day, only against the three metrics the brief names as the
    hysteresis triggers."""
    if m["price"] is None or m["price"] < config["universe_retention_price_min"]:
        return True
    if m["market_cap"] is None or m["market_cap"] < config["universe_retention_market_cap_min"]:
        return True
    if m["adv_dollar"] is None or m["adv_dollar"] < config["universe_retention_adv_min"]:
        return True
    return False


def daily_safety_reason(m: dict[str, Any], config: dict) -> str | None:
    """Immediate suspension reasons, independent of the hysteresis clock."""
    if m["price_data_gap_days"] is None or m["price_data_gap_days"] >= config[
        "universe_price_data_max_gap_days"
    ]:
        return "stale price data: no bar for " + (
            f"{m['price_data_gap_days']} of the last "
            f"{config['universe_price_data_max_gap_days']} trading sessions"
            if m["price_data_gap_days"] is not None
            else "any recent trading session"
        )
    if m["dilution_severe"]:
        return "new severe dilution evidence (dilution_signals.is_disqualified)"
    return None


# --------------------------------------------------------------- orchestration


def prior_snapshot_state(conn, security_id: int) -> dict[str, Any] | None:
    """The most recent snapshot row for this security across ALL prior runs,
    regardless of run_type -- a daily 'watch' still needs to know the status
    and counter a monthly run last recorded, and vice versa."""
    row = conn.execute(
        """
        SELECT sn.status, sn.days_below_retention, sn.snapshot_date
          FROM universe_snapshots sn
          JOIN universe_snapshot_runs r ON r.snapshot_id = sn.snapshot_id
         WHERE sn.security_id = ?
         ORDER BY sn.snapshot_date DESC, r.effective_at DESC
         LIMIT 1
        """,
        (security_id,),
    ).fetchone()
    return dict(row) if row else None


def securities_to_evaluate(
    conn, pool_versions: list[str], extra_security_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """Union of: the named candidate pool(s), the Phase F fixture (so the
    manual checklist's 'all 50 fixture securities accounted for' always
    holds), every security with a status='included' row in the most recent
    prior snapshot, every security with an open or pending_resolution
    paper position (continued monitoring, see securities_requiring_monitoring
    below -- included here too so a monitored exit isn't dropped from
    evaluation entirely), and any explicitly named extra_security_ids (used
    by tests and by targeted/debug re-evaluation)."""
    ids: set[int] = set(extra_security_ids or [])

    if pool_versions:
        placeholders = ",".join("?" for _ in pool_versions)
        ids.update(
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT security_id FROM universe_candidate_pool "
                f"WHERE pool_version IN ({placeholders})",
                pool_versions,
            )
        )

    ids.update(r[0] for r in conn.execute("SELECT DISTINCT security_id FROM fixture_manifest"))

    ids.update(
        r[0]
        for r in conn.execute(
            """
            SELECT sn.security_id
              FROM universe_snapshots sn
              JOIN (
                  SELECT security_id, MAX(snapshot_date) AS snapshot_date
                    FROM universe_snapshots GROUP BY security_id
              ) latest ON latest.security_id = sn.security_id AND latest.snapshot_date = sn.snapshot_date
             WHERE sn.status = 'included'
            """
        )
    )

    ids.update(
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT security_id FROM paper_positions pp "
            "JOIN research_candidates rc ON rc.candidate_id = pp.candidate_id "
            "WHERE pp.status IN ('open', 'pending_resolution')"
        )
    )

    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT security_id, cik, security_type, classification_confidence "
        f"FROM securities WHERE security_id IN ({placeholders})",
        list(ids),
    ).fetchall()
    return [dict(r) for r in rows]


def securities_requiring_monitoring(conn) -> set[int]:
    """Union of current official members and securities with an open paper
    position. Leaving the universe must never stop monitoring a name that
    still has capital in it."""
    included = {
        r[0]
        for r in conn.execute(
            """
            SELECT sn.security_id
              FROM universe_snapshots sn
              JOIN (
                  SELECT security_id, MAX(snapshot_date) AS snapshot_date
                    FROM universe_snapshots GROUP BY security_id
              ) latest ON latest.security_id = sn.security_id AND latest.snapshot_date = sn.snapshot_date
             WHERE sn.status = 'included'
            """
        )
    }
    open_positions = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT rc.security_id FROM paper_positions pp "
            "JOIN research_candidates rc ON rc.candidate_id = pp.candidate_id "
            "WHERE pp.status IN ('open', 'pending_resolution')"
        )
    }
    return included | open_positions


def compute_snapshot(
    conn,
    as_of_date: str,
    run_type: str,
    pool_versions: list[str] | None = None,
    rules_version: str = "s1-v1",
    extra_security_ids: list[int] | None = None,
) -> str:
    """Compute and persist one snapshot. Returns the new snapshot_id.

    Never updates or deletes a prior snapshot row -- every run is a fresh
    INSERT, so "nothing is ever removed retroactively" holds structurally.
    """
    if run_type not in ("monthly_membership", "daily_safety"):
        raise MembershipError(f"unknown run_type {run_type!r}")

    config = load_config()
    securities = securities_to_evaluate(conn, pool_versions or [], extra_security_ids)
    snapshot_id = f"universe-{uuid.uuid4().hex[:12]}"
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO universe_snapshot_runs
            (snapshot_id, effective_at, rules_version, config_hash, run_id,
             security_count, is_official, run_type)
        VALUES (?, ?, ?, ?, NULL, ?, 0, ?)
        """,
        (snapshot_id, now, rules_version, config_hash(), len(securities), run_type),
    )

    changes: list[dict[str, Any]] = []
    for security in securities:
        prior = prior_snapshot_state(conn, security["security_id"])
        metrics = gather_metrics(conn, security, as_of_date, config)
        prior_status = prior["status"] if prior else None
        prior_days_below = prior["days_below_retention"] if prior else 0

        if prior_status == "included":
            status, reason, days_below = _evaluate_included_member(
                metrics, config, run_type, prior_days_below
            )
        else:
            status, reason, days_below = _evaluate_candidate(metrics, config)

        conn.execute(
            """
            INSERT INTO universe_snapshots
                (snapshot_id, security_id, snapshot_date, status, exclusion_reason,
                 adv_dollar, market_cap, market_cap_confidence, days_below_retention)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                security["security_id"],
                as_of_date,
                status,
                reason,
                metrics["adv_dollar"],
                metrics["market_cap"],
                metrics["market_cap_confidence"],
                days_below,
            ),
        )

        if run_type == "monthly_membership" and status != (prior_status or "excluded"):
            change_type = "entered" if status == "included" else "exited"
            changes.append(
                {
                    "security_id": security["security_id"],
                    "change_type": change_type,
                    "previous_status": prior_status,
                    "new_status": status,
                    "reason": reason or "cleared all entry thresholds",
                }
            )

    for change in changes:
        conn.execute(
            """
            INSERT INTO universe_membership_changes
                (change_id, security_id, snapshot_id, change_type, effective_date,
                 previous_status, new_status, reason, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"change-{uuid.uuid4().hex[:12]}",
                change["security_id"],
                snapshot_id,
                change["change_type"],
                as_of_date,
                change["previous_status"],
                change["new_status"],
                change["reason"],
                now,
            ),
        )

    return snapshot_id


def _evaluate_candidate(
    metrics: dict[str, Any], config: dict
) -> tuple[str, str | None, int]:
    """Not currently a member (or previously excluded): must clear entry."""
    reason = entry_exclusion_reason(metrics, config)
    if reason is None:
        return "included", None, 0
    return "excluded", reason, 0


def _evaluate_included_member(
    metrics: dict[str, Any], config: dict, run_type: str, prior_days_below: int
) -> tuple[str, str | None, int]:
    """Currently a member: judged by retention, never entry, and only a
    monthly run may formally exit them."""
    safety_reason = daily_safety_reason(metrics, config)
    if run_type == "daily_safety" and safety_reason:
        # Immediate suspension. The hysteresis counter is untouched -- this
        # is a separate, faster mechanism, not a substitute for it.
        return "watch", safety_reason, prior_days_below

    if below_retention(metrics, config):
        new_days_below = prior_days_below + 1 if run_type == "daily_safety" else prior_days_below
        threshold = config["universe_retention_hysteresis_days"]
        if run_type == "monthly_membership" and new_days_below >= threshold:
            return (
                "excluded",
                f"{new_days_below} consecutive trading days below retention thresholds "
                f"(limit {threshold})",
                new_days_below,
            )
        return (
            "included",
            f"below retention thresholds for {new_days_below} of {threshold} trading days",
            new_days_below,
        )

    return "included", None, 0
