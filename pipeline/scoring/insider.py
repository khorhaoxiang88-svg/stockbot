"""The insider bonus: additive 0-10, four sub-bonuses.

Three rules drive the shape of this module.

1. AN INSIDER IS COUNTED ONCE. B1 rewards a cluster -- several different people
   buying at once. Someone who splits one decision across three tickets is one
   person, so purchases are collapsed to one q_i per insider by taking the max,
   and N counts distinct insiders. Counting tickets would let a single buyer
   manufacture a cluster.

2. AN OBSERVED ZERO IS NOT AN UNKNOWN. A company whose Form 4 history is fully
   ingested and current, with no qualifying purchase in the window, scores a
   bonus of exactly 0 and is still ranked. A company whose Form 4 history is
   stale or truncated scores UNKNOWN, which withholds ranking entirely. These
   look identical in the data and mean opposite things.

3. COMPLETENESS IS PROVED, NOT ASSUMED. F6's ingest caps each security at 60
   original Form 4s and 10 amendments, keeping the most recent, and 20,361
   filings were skipped fixture-wide. That cap is invisible in the transactions
   table, so coverage is established here from evidence: a security is complete
   for a window only if the OLDEST filing actually ingested for it predates the
   window's start. If the cap bit, the oldest ingested filing is younger than
   the window and filings inside the window may be missing -- so coverage is
   UNKNOWN and the security is withheld rather than scored on a partial record.

Qualifying purchase = Table I, transaction code P, not superseded. That is the
scored_insider_purchases view from migration 006; no query here re-defines it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# Plan-status credibility. A discretionary purchase is a decision; a purchase
# executed by a pre-set 10b5-1 plan was decided months earlier and carries half
# the weight. 'unknown' sits between them rather than defaulting either way.
PLAN_CREDIBILITY: dict[str, float] = {
    "discretionary": 1.00,
    "unknown": 0.75,
    "confirmed_10b5_1": 0.50,
}

DECAY_DAYS = 180          # d(a) = max(0, 1 - a/180)
CLUSTER_WINDOW_DAYS = 90  # B1 counts distinct insiders inside this window
SIZE_WINDOW_DAYS = 180    # B3 sums over trailing 180 days
CONVICTION_THRESHOLD = 0.25
MAX_BONUS = 10.0

# The F6 ingest caps, restated here so the coverage proof is explicit.
INGEST_MAX_ORIGINALS = 60
INGEST_MAX_AMENDMENTS = 10

_CEO = re.compile(r"\bC\.?E\.?O\.?\b|chief\s+executive", re.IGNORECASE)
_CFO = re.compile(r"\bC\.?F\.?O\.?\b|chief\s+financial", re.IGNORECASE)


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in str(value)[:10].split("-"))
    return date(year, month, day)


def credibility(plan_status: str | None) -> float:
    """c, from plan_status. Unknown is 0.75 by the frozen table, not a default."""
    return PLAN_CREDIBILITY.get(str(plan_status or "unknown"), 0.75)


def decay(age_days: float) -> float:
    """d(a) = max(0, 1 - a / 180). Zero at and beyond 180 days old."""
    return max(0.0, 1.0 - age_days / float(DECAY_DAYS))


def is_ceo_or_cfo(officer_title: str | None) -> bool:
    title = officer_title or ""
    return bool(_CEO.search(title) or _CFO.search(title))


def insider_key(row) -> str:
    """Stable identity for one person.

    insider_cik is the SEC's own identifier and is preferred. Name is the
    fallback, upper-cased and whitespace-collapsed, because the same person is
    filed as "Smith John A" and "SMITH JOHN A" across filings.
    """
    cik = row.get("insider_cik")
    if cik:
        return f"cik:{str(cik).zfill(10)}"
    name = re.sub(r"\s+", " ", str(row.get("insider_name") or "")).strip().upper()
    return f"name:{name}" if name else "unidentified"


# ------------------------------------------------------------------- coverage


@dataclass(frozen=True)
class Coverage:
    """Whether the Form 4 record for one security can be trusted for a window."""

    complete: bool
    reason: str
    detail: dict = field(default_factory=dict)


def assess_coverage(
    *,
    attempted: bool,
    run_ok: bool,
    run_finished_at: str | None,
    staleness_hours: float | None,
    sla_hours: float | None,
    originals_ingested: int,
    amendments_ingested: int,
    oldest_filed_date: str | None,
    window_start: str,
) -> Coverage:
    """Prove, or fail to prove, that the Form 4 record covers `window_start` on."""
    detail = {
        "ingest_run_finished_at": run_finished_at,
        "staleness_hours": staleness_hours,
        "freshness_sla_hours": sla_hours,
        "originals_ingested": originals_ingested,
        "amendments_ingested": amendments_ingested,
        "ingest_cap_originals": INGEST_MAX_ORIGINALS,
        "ingest_cap_amendments": INGEST_MAX_AMENDMENTS,
        "oldest_filing_ingested": oldest_filed_date,
        "window_start": window_start,
    }
    if not attempted:
        return Coverage(False, "security was not included in the Form 4 ingest", detail)
    if not run_ok:
        return Coverage(False, "no successful Form 4 ingest run is recorded", detail)
    if sla_hours is not None and staleness_hours is not None and staleness_hours > sla_hours:
        return Coverage(
            False,
            f"Form 4 ingest is stale: {staleness_hours:.1f}h since last success, "
            f"SLA is {sla_hours:.0f}h",
            detail,
        )

    cap_hit = (
        originals_ingested >= INGEST_MAX_ORIGINALS
        or amendments_ingested >= INGEST_MAX_AMENDMENTS
    )
    detail["ingest_cap_hit"] = cap_hit
    if not cap_hit:
        return Coverage(True, "every Form 4 in the ingest window was ingested", detail)

    # The cap keeps the most recent filings, so coverage is provable exactly when
    # the oldest filing we hold is older than the window we are scoring.
    if oldest_filed_date is not None and oldest_filed_date <= window_start:
        return Coverage(
            True,
            "ingest cap was reached but the oldest ingested filing predates the window",
            detail,
        )
    return Coverage(
        False,
        f"ingest cap of {INGEST_MAX_ORIGINALS} originals / {INGEST_MAX_AMENDMENTS} "
        f"amendments was reached and the oldest ingested filing "
        f"({oldest_filed_date}) is inside the {window_start} window, so filings "
        f"within the window may be missing",
        detail,
    )


# ---------------------------------------------------------------- sub-bonuses


@dataclass
class BonusParts:
    b1_cluster: float = 0.0
    b2_executive: float = 0.0
    b3_size: float = 0.0
    b4_conviction: float = 0.0
    size_ratio: float | None = None       # S, before percentile ranking
    explanation: dict = field(default_factory=dict)

    @property
    def total(self) -> float:
        return min(
            MAX_BONUS,
            self.b1_cluster + self.b2_executive + self.b3_size + self.b4_conviction,
        )


def weight_for(row, as_of: date) -> tuple[float, float, float, float]:
    """(c, age_days, d, c*d) for one purchase."""
    c = credibility(row.get("plan_status"))
    age = (as_of - parse_date(row["transaction_date"])).days
    d = decay(age)
    return c, float(age), d, c * d


def cluster_bonus(purchases: list[dict], as_of: date) -> tuple[float, dict]:
    """B1 = 4 * min(1, max(0, N - 2) / 2) * mean(q_i) over DISTINCT insiders."""
    cutoff = as_of - timedelta(days=CLUSTER_WINDOW_DAYS)
    per_insider: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for row in purchases:
        if parse_date(row["transaction_date"]) < cutoff:
            continue
        c, age, d, weight = weight_for(row, as_of)
        key = insider_key(row)
        counts[key] = counts.get(key, 0) + 1
        best = per_insider.get(key)
        if best is None or weight > best["q_i"]:
            # Purchases beyond the best one add nothing: one insider, one q_i.
            per_insider[key] = {
                "insider": key,
                "insider_name": row.get("insider_name"),
                "accession": row.get("accession_no"),
                "transaction_date": row["transaction_date"],
                "plan_status": row.get("plan_status"),
                "c": c,
                "age_days": age,
                "d": d,
                "q_i": weight,
            }
    for key, item in per_insider.items():
        item["purchase_count"] = counts[key]

    n = len(per_insider)
    detail = {
        "window_days": CLUSTER_WINDOW_DAYS,
        "distinct_insiders_N": n,
        "per_insider": sorted(per_insider.values(), key=lambda item: item["insider"]),
        "formula": "4 * min(1, max(0, N - 2) / 2) * mean(q_i)",
    }
    if n == 0:
        detail.update({"cluster_factor": 0.0, "mean_q": None, "value": 0.0})
        return 0.0, detail

    factor = min(1.0, max(0.0, (n - 2) / 2.0))
    mean_q = sum(item["q_i"] for item in per_insider.values()) / n
    value = 4.0 * factor * mean_q
    detail.update({"cluster_factor": factor, "mean_q": mean_q, "value": value})
    return value, detail


def executive_bonus(purchases: list[dict], as_of: date) -> tuple[float, dict]:
    """B2 = 2 * max(c * d) over CEO/CFO qualifying purchases."""
    best = None
    considered = []
    for row in purchases:
        if not is_ceo_or_cfo(row.get("officer_title")):
            continue
        c, age, d, weight = weight_for(row, as_of)
        entry = {
            "insider_name": row.get("insider_name"),
            "officer_title": row.get("officer_title"),
            "accession": row.get("accession_no"),
            "transaction_date": row["transaction_date"],
            "c": c, "age_days": age, "d": d, "c_times_d": weight,
        }
        considered.append(entry)
        if best is None or weight > best["c_times_d"]:
            best = entry
    value = 0.0 if best is None else 2.0 * best["c_times_d"]
    return value, {
        "formula": "2 * max(c * d) over CEO/CFO purchases",
        "candidates": considered,
        "best": best,
        "value": value,
    }


def size_ratio(purchases: list[dict], as_of: date, market_cap: float | None) -> tuple[float | None, dict]:
    """S = sum(total_value * c * d) over trailing 180 days / market_cap."""
    cutoff = as_of - timedelta(days=SIZE_WINDOW_DAYS)
    terms = []
    weighted_total = 0.0
    for row in purchases:
        if parse_date(row["transaction_date"]) < cutoff:
            continue
        total_value = row.get("total_value")
        if total_value is None:
            terms.append({
                "accession": row.get("accession_no"),
                "transaction_date": row["transaction_date"],
                "excluded": "total_value not reported",
            })
            continue
        c, age, d, weight = weight_for(row, as_of)
        term = float(total_value) * weight
        weighted_total += term
        terms.append({
            "accession": row.get("accession_no"),
            "transaction_date": row["transaction_date"],
            "total_value": float(total_value),
            "c": c, "age_days": age, "d": d, "term": term,
        })

    detail = {
        "window_days": SIZE_WINDOW_DAYS,
        "terms": terms,
        "weighted_dollar_total": weighted_total,
        "market_cap": market_cap,
        "formula": "S = sum(total_value * c * d) / market_cap",
    }
    if not market_cap or market_cap <= 0:
        detail["S"] = None
        detail["reason"] = "market cap unavailable; S cannot be computed"
        return None, detail
    s = weighted_total / float(market_cap)
    detail["S"] = s
    return s, detail


def conviction_bonus(purchases: list[dict], as_of: date) -> tuple[float, dict]:
    """B4 = 2 * max(c * d) over purchases adding > 25% to prior holdings.

    prior_holdings = shares_owned_after - purchased_shares. Zero prior holdings
    with an otherwise valid purchase is a new position and qualifies. A negative
    or internally inconsistent prior holding is marked UNKNOWN and awards
    nothing: an impossible denominator cannot be repaired by guessing at it.
    """
    best = None
    considered = []
    for row in purchases:
        shares = row.get("shares")
        owned_after = row.get("shares_owned_after")
        c, age, d, weight = weight_for(row, as_of)
        entry = {
            "insider_name": row.get("insider_name"),
            "accession": row.get("accession_no"),
            "transaction_date": row["transaction_date"],
            "purchased_shares": None if shares is None else float(shares),
            "shares_owned_after": None if owned_after is None else float(owned_after),
            "c": c, "age_days": age, "d": d, "c_times_d": weight,
        }
        if shares is None or owned_after is None or float(shares) <= 0:
            entry["status"] = "unknown"
            entry["reason"] = "purchased shares or shares owned after not reported"
            considered.append(entry)
            continue

        prior = float(owned_after) - float(shares)
        entry["prior_holdings"] = prior
        if prior < 0:
            entry["status"] = "unknown"
            entry["reason"] = (
                "shares_owned_after is smaller than the shares purchased, so prior "
                "holdings would be negative; the filing is internally inconsistent"
            )
            considered.append(entry)
            continue
        if prior == 0:
            entry["status"] = "qualifies"
            entry["reason"] = "prior holdings zero: a new position"
            entry["ratio"] = None
        else:
            ratio = float(shares) / prior
            entry["ratio"] = ratio
            if ratio > CONVICTION_THRESHOLD:
                entry["status"] = "qualifies"
                entry["reason"] = f"added {ratio:.1%} to prior holdings"
            else:
                entry["status"] = "below_threshold"
                entry["reason"] = f"added {ratio:.1%}, threshold is 25%"
        considered.append(entry)
        if entry["status"] == "qualifies" and (best is None or weight > best["c_times_d"]):
            best = entry

    value = 0.0 if best is None else 2.0 * best["c_times_d"]
    return value, {
        "formula": "2 * max(c * d) where purchased / prior_holdings > 0.25",
        "threshold": CONVICTION_THRESHOLD,
        "candidates": considered,
        "best": best,
        "value": value,
    }
