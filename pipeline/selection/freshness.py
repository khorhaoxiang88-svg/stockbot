"""Freshness, kept strictly apart from issuer-report age.

These are two different things that look identical from a distance, and F10 is
explicit that they must not be conflated:

  PIPELINE FRESHNESS is about us. Did the scheduled ingestion run, did it
  succeed, and was that recently enough? A pipeline that has not run blocks
  every new candidate, because we would be selecting on numbers we cannot
  vouch for.

  ISSUER-REPORT AGE is about the company. A company that last filed a 10-K
  eleven months ago is not stale -- that is simply how often companies file.
  Treating an old-but-current filing as stale would block every well-behaved
  annual filer for eleven months of every year.

So the SEC check asks whether OUR INGESTION succeeded on schedule, never
whether the newest fact is recent. Separately, if a company appears to have
missed its own filing deadline, that is real information about the company and
is surfaced as a risk flag (overdue_issuer_filing) rather than being folded into
staleness, because it must not block selection and it is not a pipeline fault.

FILER CATEGORY IS UNKNOWN, so the deadlines used are the most permissive ones.
SEC Company Facts does not return dei:EntityFilerCategory -- there are zero such
facts in the fixture -- so we cannot tell a large accelerated filer (10-K in 60
days) from a non-accelerated one (90 days). The non-accelerated schedule is
used throughout. That can only ever under-report an overdue filer, never invent
one, which is the correct direction for a flag that reads as an accusation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

# Non-accelerated filer deadlines, in calendar days after the period end.
# The most permissive schedule, deliberately: see the module docstring.
FORM_10K_DEADLINE_DAYS = 90
FORM_10Q_DEADLINE_DAYS = 45
# Grace beyond the deadline before anything is called overdue. Form 12b-25
# ("NT 10-K") buys an automatic 15 days for an annual report and 5 for a
# quarterly, and we do not track 12b-25 filings, so the longer one is allowed
# to both.
LATE_FILING_GRACE_DAYS = 15

# Which pipeline stage answers for which configured SLA source.
STAGE_FOR_SOURCE: dict[str, tuple[str, ...]] = {
    "symbol_master": ("fixture_load",),
    "prices_daily": ("price_ingest",),
    "fundamentals": ("fundamentals",),
    "corporate_actions": ("price_ingest",),
    "filings": ("sec_facts", "insider"),
}


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in str(value)[:10].split("-"))
    return date(year, month, day)


def hours_between(earlier: str, later: str) -> float:
    """Whole hours between two UTC timestamps, floored at zero."""
    from datetime import datetime

    def parse(value: str) -> datetime:
        return datetime.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S")

    return max(0.0, (parse(later) - parse(earlier)).total_seconds() / 3600.0)


@dataclass
class SourceStatus:
    source: str
    ok: bool
    detail: str
    sla_hours: float | None = None
    age_hours: float | None = None
    last_success: str | None = None


@dataclass
class FreshnessReport:
    statuses: list[SourceStatus] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(status.ok for status in self.statuses)

    @property
    def failures(self) -> list[SourceStatus]:
        return [status for status in self.statuses if not status.ok]

    def as_json(self) -> list[dict]:
        return [
            {
                "source": s.source, "ok": s.ok, "detail": s.detail,
                "sla_hours": s.sla_hours, "age_hours": s.age_hours,
                "last_success": s.last_success,
            }
            for s in self.statuses
        ]


def stage_failures(conn, stage: str, run_id: str) -> set[str]:
    """Which securities a partial run failed on, from its errors_json.

    A run marked 'partial' is not a blanket failure. The F4 fact ingest reports
    'partial' because SPY has no companyfacts endpoint at all -- it is a unit
    investment trust and files N-CSR, so a 404 there is the correct answer, not
    an outage. Blocking every candidate over it would be wrong, and quietly
    ignoring it would be worse, so the failure is narrowed to the securities the
    run actually names.
    """
    row = conn.execute(
        "SELECT errors_json FROM pipeline_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None or not row["errors_json"]:
        return set()
    try:
        entries = json.loads(row["errors_json"])
    except (TypeError, ValueError):
        return set()
    text = " ".join(str(entry) for entry in entries)
    return {
        str(r["symbol"])
        for r in conn.execute(
            "SELECT DISTINCT symbol FROM listings"
        )
        if str(r["symbol"]) and str(r["symbol"]) in text
    }


def check_pipeline_freshness(conn, cutoff_utc: str, now_utc: str, cfg) -> FreshnessReport:
    """Is OUR ingestion current, and does it cover the cutoff?

    Two conditions, on two different axes, and conflating them is the trap.

      COVERAGE is measured against the cutoff. A run that finished before the
      week's close cannot have seen that week's close, so it cannot vouch for
      it however recently it ran.

      AGE is measured against NOW, not against the cutoff. "Has the scheduled
      ingestion run recently enough" is a question about the pipeline's health
      at the moment of selection. Measuring it against the cutoff would make a
      correct backfill look stale simply because it ran after the period it
      ingested -- which is what a backfill always does.

    Point-in-time correctness is not this function's job. It belongs to the
    evidence cutoff, which filters individual accessions by accepted_at. The two
    are orthogonal: ingest broadly, then use only what was public at the cutoff.
    """
    report = FreshnessReport()
    sla = {k: v for k, v in (cfg.get("freshness_sla") or {}).items() if not k.startswith("_")}

    for source, budget in sorted(sla.items()):
        stages = STAGE_FOR_SOURCE.get(source)
        if not stages:
            report.statuses.append(SourceStatus(
                source, False,
                f"no pipeline stage is mapped to the configured SLA source "
                f"'{source}', so its freshness cannot be established",
                float(budget),
            ))
            continue

        for stage in stages:
            row = conn.execute(
                "SELECT run_id, status, finished_at FROM pipeline_runs "
                "WHERE stage = ? AND status IN ('success', 'partial') "
                "AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1",
                (stage,),
            ).fetchone()
            if row is None:
                report.statuses.append(SourceStatus(
                    f"{source}:{stage}", False,
                    f"no successful '{stage}' run has ever been recorded",
                    float(budget),
                ))
                continue

            age = hours_between(row["finished_at"], now_utc)
            if row["finished_at"] < cutoff_utc:
                report.statuses.append(SourceStatus(
                    f"{source}:{stage}", False,
                    f"the newest '{stage}' run finished {row['finished_at']}, before "
                    f"the evidence cutoff {cutoff_utc}, so it cannot cover it",
                    float(budget), age, row["finished_at"],
                ))
                continue
            if age > float(budget):
                report.statuses.append(SourceStatus(
                    f"{source}:{stage}", False,
                    f"last succeeded {row['finished_at']}, {age:.0f}h ago against a "
                    f"{budget}h SLA. Re-run the '{stage}' stage",
                    float(budget), age, row["finished_at"],
                ))
                continue

            note = ""
            if row["status"] == "partial":
                failed = stage_failures(conn, stage, row["run_id"])
                note = (
                    f"; the run was partial and named {len(failed)} security(ies), "
                    f"which are excluded individually rather than blocking the run"
                    if failed else "; the run was partial but named no security"
                )
            report.statuses.append(SourceStatus(
                f"{source}:{stage}", True,
                f"last succeeded {row['finished_at']}, {age:.0f}h ago, SLA "
                f"{budget}h{note}",
                float(budget), age, row["finished_at"],
            ))

    return report


def check_latest_session_present(conn, final_session: str) -> SourceStatus:
    """The week's final regular session must actually be in the price data."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT security_id) AS n FROM prices WHERE date = ?",
        (final_session,),
    ).fetchone()
    present = int(row["n"] or 0)
    if present == 0:
        newest = conn.execute("SELECT MAX(date) AS d FROM prices").fetchone()["d"]
        return SourceStatus(
            "prices:latest_session", False,
            f"the week's final regular session {final_session} has no bars; the "
            f"newest session in the dataset is {newest}",
        )
    return SourceStatus(
        "prices:latest_session", True,
        f"{present} securities have a bar for the final session {final_session}",
    )


def check_form4_coverage(conn, cutoff_utc: str, now_utc: str, cfg) -> SourceStatus:
    """Form 4 ingestion must have succeeded AND cover filings up to the cutoff.

    Two separate conditions, and passing only the first is the trap. A run that
    succeeded an hour ago still proves nothing if it was told to stop at last
    Tuesday.
    """
    run = conn.execute(
        "SELECT run_id, finished_at FROM pipeline_runs WHERE stage = 'insider' "
        "AND status IN ('success', 'partial') AND finished_at IS NOT NULL "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    budget = (cfg.get("freshness_sla") or {}).get("filings")
    if run is None:
        return SourceStatus(
            "form4:pipeline", False, "no successful Form 4 ingest has ever been recorded",
            None if budget is None else float(budget),
        )
    age = hours_between(run["finished_at"], now_utc)
    if budget is not None and age > float(budget):
        return SourceStatus(
            "form4:pipeline", False,
            f"Form 4 ingest last succeeded {run['finished_at']}, {age:.0f}h ago "
            f"against a {budget}h SLA. Re-run the 'insider' stage",
            float(budget), age, run["finished_at"],
        )

    covered = conn.execute(
        "SELECT MAX(accepted_at) AS newest FROM insider_transactions "
        "WHERE accepted_at IS NOT NULL AND accepted_at <= ?",
        (cutoff_utc,),
    ).fetchone()["newest"]
    if covered is None:
        return SourceStatus(
            "form4:coverage", False,
            "no Form 4 rows with a resolvable acceptance time at or before the cutoff",
            None if budget is None else float(budget), age, run["finished_at"],
        )
    # "Covers filings accepted through the candidate cutoff" means the ingest
    # finished AFTER the cutoff, so everything accepted up to the cutoff had
    # already been published when it ran.
    if run["finished_at"] < cutoff_utc:
        return SourceStatus(
            "form4:coverage", False,
            f"the Form 4 ingest finished {run['finished_at']}, before the evidence "
            f"cutoff {cutoff_utc}, so it cannot cover filings accepted up to it",
            None if budget is None else float(budget), age, run["finished_at"],
        )
    return SourceStatus(
        "form4:coverage", True,
        f"ingest finished {run['finished_at']}, covering acceptances through {covered}",
        None if budget is None else float(budget), age, run["finished_at"],
    )


# --------------------------------------------------------- issuer-report age


@dataclass(frozen=True)
class IssuerFilingStatus:
    overdue: bool
    determinable: bool
    detail: str
    accession: str | None = None


def check_issuer_filing_schedule(conn, cik: str | None, as_of: str) -> IssuerFilingStatus:
    """Does this company look overdue on its own filings?

    NOT a freshness check and never blocks selection. A company falling behind
    on its reporting is information about the company.
    """
    if not cik:
        return IssuerFilingStatus(
            False, False, "no CIK, so no filing schedule can be established"
        )

    row = conn.execute(
        """
        SELECT accession_no, form_type, filed_date, period_of_report
          FROM filings
         WHERE cik = ? AND form_type IN ('10-K', '10-Q') AND filed_date <= ?
         ORDER BY filed_date DESC LIMIT 1
        """,
        (str(cik).zfill(10), as_of),
    ).fetchone()
    if row is None:
        return IssuerFilingStatus(
            False, False,
            "no 10-K or 10-Q on file at or before the as-of date, so no schedule "
            "can be established (foreign private issuers file 20-F, trusts N-CSR)",
        )
    if not row["period_of_report"]:
        return IssuerFilingStatus(
            False, False,
            f"the newest periodic filing {row['accession_no']} has no period of "
            f"report recorded, so its deadline cannot be computed",
            row["accession_no"],
        )

    # The NEXT report is due one period after the last one filed.
    period_end = parse_date(row["period_of_report"])
    if row["form_type"].startswith("10-K"):
        next_period_end = period_end + timedelta(days=91)      # next quarter
        deadline_days = FORM_10Q_DEADLINE_DAYS
        expected = "10-Q"
    else:
        next_period_end = period_end + timedelta(days=91)
        deadline_days = FORM_10Q_DEADLINE_DAYS
        expected = "10-Q or 10-K"
    due = next_period_end + timedelta(days=deadline_days + LATE_FILING_GRACE_DAYS)

    today = parse_date(as_of)
    if today <= due:
        return IssuerFilingStatus(
            False, True,
            f"last periodic filing {row['form_type']} {row['accession_no']} covers "
            f"the period ending {row['period_of_report']}; the next {expected} is "
            f"not due until {due.isoformat()} on the non-accelerated schedule",
            row["accession_no"],
        )
    return IssuerFilingStatus(
        True, True,
        f"last periodic filing {row['form_type']} {row['accession_no']} covers the "
        f"period ending {row['period_of_report']}. The next {expected} would cover "
        f"the period ending about {next_period_end.isoformat()} and was due by "
        f"{due.isoformat()} even on the most permissive (non-accelerated) schedule "
        f"with the full 12b-25 grace period, {(today - due).days} days ago. Filer "
        f"category is unknown because SEC Company Facts does not return "
        f"dei:EntityFilerCategory, so the longest deadline was assumed.",
        row["accession_no"],
    )
