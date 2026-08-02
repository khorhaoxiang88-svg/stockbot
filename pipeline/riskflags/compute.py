"""Compute the risk flags for the fixture.

Everything except going concern is resolved from data already in the database.
Going concern reads the actual filing text, because the phrase it looks for is
not in XBRL: ASC 205-40 disclosure is narrative, and the auditor's explanatory
paragraph is narrative too. That means one or two document fetches per security.

Order: cheap deterministic checks first, network last, so a network failure
leaves every other flag intact and turns only going_concern into an unknown.

EVERY SECURITY GETS FLAGS, including the ones F8 refuses to rank. A security
that cannot be scored is exactly the one a reader most needs risk evidence for,
and "no score" must not also mean "no information".
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from fundamentals import metrics as M  # noqa: E402
from fundamentals.compute import FactIndex  # noqa: E402
from fundamentals.mappings import CONCEPT_MAP  # noqa: E402
from riskflags import altman as ALT  # noqa: E402
from riskflags import detectors as D  # noqa: E402
from riskflags import going_concern as GC  # noqa: E402
from sec.payload_store import utc_now  # noqa: E402
from universe.sec_client import SecClient, load_dotenv_into_environ  # noqa: E402

CODE_VERSION = "riskflags/v1"

# Retained earnings is the one Z'' input F5 does not map. F5's mapping is frozen
# at MAPPING_VERSION and every derived_fundamentals row records that version, so
# adding a concept there would change the provenance of numbers this phase does
# not touch. It is resolved here instead, through the same FactIndex machinery.
RETAINED_EARNINGS = [
    ("us-gaap", "RetainedEarningsAccumulatedDeficit"),
    ("us-gaap", "RetainedEarningsAccumulatedDeficitIncludingPortionAttributableToNoncontrollingInterest"),
]

ALTMAN_INPUTS = (
    "current_assets", "current_liabilities", "stockholders_equity", "assets",
    "liabilities", "operating_income", "pretax_income", "interest_expense",
)
CASH_FLOW_INPUTS = ("cfo", "capex")

GOING_CONCERN_FORMS = ("10-K", "10-Q")


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in str(value)[:10].split("-"))
    return date(year, month, day)


def fixture_securities(conn) -> list[dict]:
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


def fundamentals_at(conn, security_id: int, cutoff: str):
    """Newest fiscal period knowable at the cutoff, newest knowledge state."""
    period = conn.execute(
        "SELECT MAX(period_end) AS period_end FROM derived_fundamentals "
        "WHERE security_id = ? AND knowledge_date <= ?",
        (security_id, cutoff),
    ).fetchone()
    if not period or not period["period_end"]:
        return None
    return conn.execute(
        "SELECT * FROM derived_fundamentals WHERE security_id = ? AND period_end = ? "
        "AND knowledge_date <= ? ORDER BY knowledge_date DESC LIMIT 1",
        (security_id, period["period_end"], cutoff),
    ).fetchone()


# ------------------------------------------------------------------ detectors


def cash_flow_and_altman(conn, security: dict, row, cutoff: str) -> list[D.Flag]:
    """CFO, FCF and Z''. All four need facts F5 does not store as columns."""
    codes = ("negative_operating_cash_flow", "negative_free_cash_flow", "altman_distress")
    if row is None or not security["cik"]:
        why = (
            "no derived fundamentals exist for this security at the knowledge cutoff"
            if security["cik"] else "no CIK, so no SEC facts can be resolved"
        )
        return [D.unknown(code, why) for code in codes]

    period_end = row["period_end"]
    knowledge_date = row["knowledge_date"]
    wanted = set(CASH_FLOW_INPUTS) | set(ALTMAN_INPUTS)
    concepts = {pair for name in wanted for pair in
                [(t, c) for t, c, *_ in CONCEPT_MAP.get(name, [])]}
    concepts |= set(RETAINED_EARNINGS)
    index = FactIndex(conn, str(security["cik"]).zfill(10), concepts)

    def resolve(name: str) -> M.Input:
        return index.resolve(
            name, [(t, c) for t, c, *_ in CONCEPT_MAP.get(name, [])], period_end, knowledge_date
        )

    cfo = resolve("cfo")
    capex = resolve("capex")
    flags = [
        D.negative_operating_cash_flow(cfo, period_end),
        D.negative_free_cash_flow(cfo, capex, period_end),
    ]

    retained = index.resolve("retained_earnings", RETAINED_EARNINGS, period_end, knowledge_date)
    resolved = {name: resolve(name) for name in ALTMAN_INPUTS}
    result = ALT.compute(
        current_assets=resolved["current_assets"].value if resolved["current_assets"].present else None,
        current_liabilities=resolved["current_liabilities"].value if resolved["current_liabilities"].present else None,
        retained_earnings=retained.value if retained.present else None,
        operating_income=resolved["operating_income"].value if resolved["operating_income"].present else None,
        pretax_income=resolved["pretax_income"].value if resolved["pretax_income"].present else None,
        interest_expense=resolved["interest_expense"].value if resolved["interest_expense"].present else None,
        equity=resolved["stockholders_equity"].value if resolved["stockholders_equity"].present else None,
        assets=resolved["assets"].value if resolved["assets"].present else None,
        liabilities=resolved["liabilities"].value if resolved["liabilities"].present else None,
        source_accession=resolved["assets"].accession if resolved["assets"].present else None,
    )
    flags.append(altman_flag(result, period_end, int(row["model_applicable"])))
    return flags


def altman_flag(result: ALT.AltmanResult, period_end: str, model_applicable: int) -> D.Flag:
    if not result.computable:
        return D.unknown(
            "altman_distress",
            "Z'' inputs missing for " + period_end + ": " + ", ".join(result.missing),
        )
    if result.source_accession is None:
        return D.unknown("altman_distress", "Z'' computed but the reporting accession is unknown")

    caveat = " " + ALT.CAVEAT
    if not model_applicable:
        caveat = (
            " This security carries model_applicable = 0 from F5 (SIC division H), "
            "so Z'' is especially unreliable for it." + caveat
        )
    detail = (
        f"Z'' = 6.56({result.terms['x1']['used']:.4f}) + 3.26({result.terms['x2']['used']:.4f})"
        f" + 6.72({result.terms['x3']['used']:.4f}) + 1.05({result.terms['x4']['used']:.4f})"
        f" = {result.z_double_prime:.3f}, EBIT taken as {result.ebit_basis}."
    )
    if result.winsorised:
        detail += " Winsorised: " + "; ".join(result.winsorised) + "."

    if result.is_distress:
        return D.Flag(
            "altman_distress", "high",
            f"Z'' for {period_end} is {result.z_double_prime:.2f}, below the "
            f"distress threshold of {ALT.DISTRESS_THRESHOLD}. {detail}{caveat}",
            result.source_accession,
        )
    return D.Flag(
        "altman_distress", "none",
        f"Not detected. Z'' for {period_end} is {result.z_double_prime:.2f} "
        f"({result.zone} zone), at or above {ALT.DISTRESS_THRESHOLD}. {detail}{caveat}",
        result.source_accession,
    )


def leverage_flags(row, cfg) -> list[D.Flag]:
    if row is None:
        return [
            D.unknown(code, "no derived fundamentals at the knowledge cutoff")
            for code in ("high_leverage", "low_interest_coverage")
        ]
    return [
        D.high_leverage(
            row["debt_ebitda"], row["debt_ebitda_accession"],
            float(cfg["high_leverage_debt_ebitda"]), row["period_end"],
        ),
        D.low_interest_coverage(
            row["interest_coverage"], row["interest_coverage_accession"],
            float(cfg["interest_coverage_cap"]), row["period_end"],
        ),
    ]


def share_flags(conn, security: dict, as_of_date: str) -> list[D.Flag]:
    security_id = int(security["security_id"])
    dilution = conn.execute(
        "SELECT * FROM dilution_signals WHERE security_id = ? AND as_of_date <= ? "
        "ORDER BY as_of_date DESC LIMIT 1",
        (security_id, as_of_date),
    ).fetchone()

    flags: list[D.Flag] = []
    if dilution is None:
        flags.append(D.unknown(
            "rapid_share_growth", "no dilution signal has been computed for this security"
        ))
        flags += [D.unknown(code, "no dilution signal has been computed for this security")
                  for code in ("shelf_capacity", "active_issuance", "atm_or_convertible")]
    else:
        detail = share_growth_detail(dilution["classification_notes"])
        flags.append(D.rapid_share_growth(
            dilution["shares_yoy_growth"],
            (detail or {}).get("latest_accession"),
            detail,
        ))
        evidence = json.loads(dilution["evidence_json"] or "[]")
        flags += D.dilution_flags(evidence, dilution["classification_notes"])

    cutoff = (parse_date(as_of_date) - timedelta(days=D.REVERSE_SPLIT_LOOKBACK_DAYS)).isoformat()
    action = conn.execute(
        "SELECT ex_date, ratio, provider FROM corporate_actions "
        "WHERE security_id = ? AND action_type = 'split' AND ratio IS NOT NULL "
        "AND ratio < 1.0 AND requires_manual_review = 0 AND ex_date >= ? AND ex_date <= ? "
        "ORDER BY ex_date DESC LIMIT 1",
        (security_id, cutoff, as_of_date),
    ).fetchone()
    flags.append(D.recent_reverse_split(
        dict(action) if action else None, security_id, cutoff
    ))
    return flags


def share_growth_detail(notes: str | None) -> dict | None:
    """F7 stored the share-growth basis as JSON inside classification_notes."""
    if not notes:
        return None
    marker = "share growth basis: "
    index = notes.find(marker)
    if index < 0:
        return None
    try:
        return json.loads(notes[index + len(marker):].split(" | ")[0])
    except json.JSONDecodeError:
        return None


def insider_flag(conn, security_id: int, as_of_date: str) -> D.Flag:
    """Sales in the trailing window. Coverage is proved the same way F8 proves it."""
    counts = conn.execute(
        "SELECT COUNT(DISTINCT CASE WHEN is_amendment = 0 THEN accession_no END) AS originals, "
        "COUNT(DISTINCT CASE WHEN is_amendment = 1 THEN accession_no END) AS amendments, "
        "MIN(filed_date) AS oldest FROM insider_transactions WHERE security_id = ?",
        (security_id,),
    ).fetchone()
    window_start = (
        parse_date(as_of_date) - timedelta(days=D.INSIDER_SELLING_WINDOW_DAYS)
    ).isoformat()

    cap_hit = (int(counts["originals"] or 0) >= 60) or (int(counts["amendments"] or 0) >= 10)
    complete = True
    reason = ""
    if int(counts["originals"] or 0) == 0 and int(counts["amendments"] or 0) == 0:
        complete = False
        reason = "no Form 4 filings are recorded for this security"
    elif cap_hit and (counts["oldest"] is None or counts["oldest"] > window_start):
        complete = False
        reason = (
            f"the F6 ingest cap was reached and the oldest ingested filing "
            f"({counts['oldest']}) is inside the {window_start} window, so sales "
            f"within the window may be missing"
        )

    sales = [
        dict(row)
        for row in conn.execute(
            "SELECT accession_no, insider_name, insider_cik, transaction_date, "
            "total_value FROM effective_insider_transactions "
            "WHERE security_id = ? AND table_type = 'I' AND transaction_code = 'S' "
            "AND transaction_date >= ? AND transaction_date <= ? "
            "ORDER BY transaction_date DESC",
            (security_id, window_start, as_of_date),
        )
    ]
    return D.recent_insider_selling(sales, complete, reason)


def data_completeness_flag(conn, security: dict, row, as_of_date: str, cfg) -> D.Flag:
    """Which fields are missing, and which source should have supplied them."""
    problems: list[dict] = []
    security_id = int(security["security_id"])

    if row is None:
        problems.append({
            "source": "sec_companyfacts",
            "detail": "no derived fundamentals at the knowledge cutoff, so every "
                      "fundamental field is missing",
            "blocking": True,
        })
    else:
        missing = json.loads(row["missing_fields_json"] or "{}")
        if missing:
            fields = sorted(missing) if isinstance(missing, dict) else sorted(map(str, missing))
            problems.append({
                "source": "sec_companyfacts",
                "detail": f"{len(fields)} field(s) unresolved for period "
                          f"{row['period_end']}: {', '.join(fields[:12])}"
                          + (" ..." if len(fields) > 12 else ""),
                "blocking": False,
            })

    bars = conn.execute(
        "SELECT COUNT(*) AS n, MAX(date) AS newest FROM prices WHERE security_id = ?",
        (security_id,),
    ).fetchone()
    # Staleness is measured against the newest bar in the dataset, not against
    # as_of_date. as_of_date can be a weekend or a holiday, and flagging every
    # security as stale every Saturday would be noise, not evidence.
    market = conn.execute(
        "SELECT MAX(date) AS newest FROM prices WHERE date <= ?", (as_of_date,)
    ).fetchone()
    if int(bars["n"] or 0) == 0:
        problems.append({
            "source": "prices:yfinance",
            "detail": "no price bars at all, so momentum, market cap and the "
                      "52-week range cannot be computed",
            "blocking": True,
        })
    elif market and market["newest"] and bars["newest"] < market["newest"]:
        problems.append({
            "source": "prices:yfinance",
            "detail": f"newest bar for this security is {bars['newest']}, while the "
                      f"dataset reaches {market['newest']}",
            "blocking": False,
        })

    sla = (cfg.get("freshness_sla") or {})
    for health in conn.execute("SELECT * FROM source_health"):
        budget = sla.get(health["source_name"])
        if health["last_error"] and health["consecutive_failures"]:
            problems.append({
                "source": health["source_name"],
                "detail": f"{health['consecutive_failures']} consecutive failures, "
                          f"last error {health['last_error']}",
                "blocking": True,
            })
        elif budget is not None and health["last_success"]:
            age_hours = (
                parse_date(as_of_date).toordinal()
                - parse_date(health["last_success"]).toordinal()
            ) * 24.0
            if age_hours > float(budget):
                problems.append({
                    "source": health["source_name"],
                    "detail": f"last success {health['last_success']}, "
                              f"{age_hours:.0f}h old against a {budget}h SLA",
                    "blocking": False,
                })

    accession = row["pe_accession"] if row is not None else None
    return D.stale_or_incomplete_data(problems, accession)


# ------------------------------------------------------------- going concern


def going_concern_flag(conn, sec, security: dict, as_of_date: str) -> D.Flag:
    """Narrow phrase detector over the latest 10-K and 10-Q."""
    cik = security["cik"]
    if not cik:
        return D.unknown("going_concern", "no CIK, so no filings can be located")

    filings = []
    for form in GOING_CONCERN_FORMS:
        row = conn.execute(
            "SELECT accession_no, form_type, filed_date, primary_doc_url FROM filings "
            "WHERE cik = ? AND form_type = ? AND filed_date <= ? "
            "AND primary_doc_url IS NOT NULL ORDER BY filed_date DESC LIMIT 1",
            (str(cik).zfill(10), form, as_of_date),
        ).fetchone()
        if row:
            filings.append(dict(row))
    if not filings:
        return D.unknown(
            "going_concern",
            "no 10-K or 10-Q with a retrievable primary document is on file at or "
            "before the as-of date",
        )

    problems: list[str] = []
    for filing in filings:
        result = GC.scan_stream(sec, filing["primary_doc_url"])
        if result.error is not None:
            problems.append(
                f"{filing['form_type']} {filing['accession_no']}: fetch failed, {result.error}"
            )
            continue
        match = result.match
        if match is not None and match.detected:
            severity = "medium" if match.alleviated else "high"
            alleviation = (
                " The filing states the doubt was ALLEVIATED by management's plans, "
                "which removes the auditor's explanatory paragraph but not the fact "
                "that the condition was identified."
                if match.alleviated else ""
            )
            return D.Flag(
                "going_concern", severity,
                f"Substantial-doubt language located in {filing['form_type']} "
                f"{filing['accession_no']} filed {filing['filed_date']}, at "
                f"character offset {match.offset:,} of the extracted text. "
                f"Passage: “{match.passage}”.{alleviation} "
                f"Source: {filing['primary_doc_url']}",
                filing["accession_no"],
            )
        if match is None:
            problems.append(
                f"{filing['form_type']} {filing['accession_no']}: document could not be scanned"
            )

    if problems:
        return D.unknown("going_concern", "; ".join(problems))

    newest = filings[0]
    return D.Flag(
        "going_concern", "none",
        f"Not detected. The standard substantial-doubt construction does not "
        f"appear in {', '.join(f'{f['form_type']} {f['accession_no']}' for f in filings)}. "
        f"This detector matches only the fixed phrasing required by ASC 205-40 and "
        f"AS 2415; it is not a general language classifier.",
        newest["accession_no"],
    )


# ---------------------------------------------------------------------- main


def compute_security(conn, sec, security: dict, as_of_date: str, cutoff: str,
                     cfg, skip_network: bool) -> list[dict]:
    row = fundamentals_at(conn, int(security["security_id"]), cutoff)
    flags: list[D.Flag] = []
    flags += cash_flow_and_altman(conn, security, row, cutoff)
    flags += leverage_flags(row, cfg)
    flags += share_flags(conn, security, as_of_date)
    flags.append(insider_flag(conn, int(security["security_id"]), as_of_date))
    flags.append(data_completeness_flag(conn, security, row, as_of_date, cfg))

    if skip_network:
        flags.append(D.unknown(
            "going_concern", "document fetching was disabled for this run (--no-network)"
        ))
    else:
        flags.append(going_concern_flag(conn, sec, security, as_of_date))

    # dilution_flags can emit a second stale_or_incomplete_data unknown when the
    # F7 classification cap bit. The primary key allows one row per flag_code, so
    # the detected row wins and the cap note is folded into it.
    merged: dict[str, D.Flag] = {}
    for flag in flags:
        existing = merged.get(flag.flag_code)
        if existing is None:
            merged[flag.flag_code] = flag
        elif existing.is_unknown and not flag.is_unknown:
            merged[flag.flag_code] = flag
        elif not existing.is_unknown and flag.is_unknown:
            merged[flag.flag_code] = D.Flag(
                existing.flag_code, existing.severity,
                existing.evidence_text + " Also: " + flag.evidence_text,
                existing.source_accession, False,
            )
    return [flag.as_row(int(security["security_id"]), as_of_date)
            for flag in merged.values()]


def report(rows: list[dict], securities: dict[int, str]) -> str:
    by_security: dict[int, list[dict]] = {}
    for row in rows:
        by_security.setdefault(row["security_id"], []).append(row)

    lines = ["", f"{'SYM':<8}{'DETECTED':>9}{'UNKNOWN':>9}{'CLEAN':>7}{'CTX':>5}  FLAGS DETECTED"]
    severity_totals: dict[str, int] = {}
    for security_id in sorted(by_security, key=lambda k: securities.get(k, "")):
        items = by_security[security_id]
        detected = [i for i in items if i["severity"] in ("high", "medium", "low")]
        unknowns = [i for i in items if i["severity"] == "unknown"]
        clean = [i for i in items if i["severity"] == "none"]
        context = [i for i in items if i["severity"] == "context"]
        for item in items:
            severity_totals[item["severity"]] = severity_totals.get(item["severity"], 0) + 1
        names = ",".join(
            f"{i['flag_code']}({i['severity'][0]})"
            for i in sorted(detected, key=lambda i: i["flag_code"])
        )
        lines.append(
            f"{securities.get(security_id, str(security_id)):<8}{len(detected):>9}"
            f"{len(unknowns):>9}{len(clean):>7}{len(context):>5}  {names}"
        )
    lines += ["", "SEVERITY DISTRIBUTION"]
    for severity in ("high", "medium", "low", "none", "context", "unknown"):
        lines.append(f"  {severity:<9}{severity_totals.get(severity, 0):>6}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute risk flags")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--no-network", action="store_true",
                        help="skip going-concern document fetching; it becomes an unknown")
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    load_dotenv_into_environ()
    conn = migrate.connect(Path(args.db))
    sec = None if args.no_network else SecClient()
    run_id = f"riskflags-{uuid.uuid4().hex[:12]}"

    try:
        conn.execute("BEGIN")
        # as_of_date is used verbatim, NOT snapped back to the last trading
        # session the way a score date is. A risk flag is a statement about
        # filings and accounts, which arrive on calendar days; snapping would
        # push the panel behind evidence that was already public.
        as_of_date = args.as_of
        cutoff = f"{as_of_date}T23:59:59Z"

        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'riskflags', ?, 'running', ?)",
            (run_id, utc_now(), CODE_VERSION),
        )

        securities = fixture_securities(conn)
        if args.symbols:
            wanted = {s.upper() for s in args.symbols}
            securities = [s for s in securities if (s["symbol"] or "").upper() in wanted]

        all_rows: list[dict] = []
        names = {int(s["security_id"]): s["symbol"] for s in securities}
        for security in securities:
            rows = compute_security(conn, sec, security, as_of_date, cutoff, cfg,
                                    args.no_network)
            for row in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO risk_flags (security_id, as_of_date, "
                    "flag_code, severity, evidence_text, source_accession, is_unknown) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["security_id"], row["as_of_date"], row["flag_code"],
                     row["severity"], row["evidence_text"], row["source_accession"],
                     row["is_unknown"]),
                )
            all_rows += rows
            detected = sum(1 for r in rows if r["severity"] in ("high", "medium", "low"))
            unknowns = sum(1 for r in rows if r["severity"] == "unknown")
            print(f"{security['symbol']:<8} {detected} detected, {unknowns} unknown")

        conn.execute(
            "UPDATE pipeline_runs SET status='success', finished_at=?, records_written=? "
            "WHERE run_id=?",
            (utc_now(), len(all_rows), run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print(report(all_rows, names))
    print(f"\nrisk flag rows written: {len(all_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
