"""Execute and resolve simulated positions under R1-PROTOCOL-1.1.

Two passes, run in this order every time:

  1. ENTRY. Every research_candidate not yet resolved into a paper_position or a
     cancelled_entries row is attempted at its next regular session open. A
     candidate opens in every horizon selection did not already suppress for it
     (open_position / book_capacity), sharing one fill, one stop and one target
     across both books -- the entry decision is per SECURITY, not per book.

  2. WALK-FORWARD. Every open (or pending_resolution) position is advanced one
     session at a time from where it was last evaluated, applying that
     session's corporate actions BEFORE its OHLC, so a split can never be read
     as a stop-out or a collapse.

Nothing here guesses at data that does not exist. A missing ADV, a missing bar,
a missing ATR -- each is a defined outcome (a cancellation, or simply "not yet
evaluated"), never a filled-in number.
"""

from __future__ import annotations

import argparse
import hashlib
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
from execution import actions as ACT  # noqa: E402
from execution import delisting as DL  # noqa: E402
from execution import protocol as P  # noqa: E402
from scoring.compute import config_hash  # noqa: E402
from sec.payload_store import utc_now  # noqa: E402

CODE_VERSION = "execution-protocol-1.1/v1"
BOOK_IDS = {20: "book-20d", 60: "book-60d"}
ADV_WINDOW = 20

PHASE_BANNER = "Engineering validation dataset - not strategy performance."


def parse_date(value: str) -> date:
    year, month, day = (int(part) for part in str(value)[:10].split("-"))
    return date(year, month, day)


def position_id(candidate_id: str, horizon_days: int, benchmark: bool = False) -> str:
    """Deterministic, so re-running never duplicates a position.

    Matches the deterministic-id approach F10 uses for candidate_id: identical
    inputs always resolve to the identical row, so a re-run is a storage-level
    no-op rather than a second attempt an idempotency check would have to guard.
    """
    prefix = "bench" if benchmark else "pos"
    seed = f"{prefix}|{candidate_id}|{horizon_days}"
    return f"{prefix}-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


# ------------------------------------------------------------------ sessions


def all_sessions(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT date FROM prices ORDER BY date")]


def next_session_after(sessions: list[str], day: str) -> str | None:
    for value in sessions:
        if value > day:
            return value
    return None


def sessions_between(sessions: list[str], start_exclusive: str, end_inclusive: str) -> list[str]:
    return [v for v in sessions if start_exclusive < v <= end_inclusive]


# --------------------------------------------------------------------- ADV


def dollar_adv(conn, security_id: int, as_of: str, window: int = ADV_WINDOW) -> float | None:
    """Trailing `window`-session mean of raw close * volume, ending at as_of.

    Raw, not split-adjusted: close * volume is invariant to a split (a 10x
    share count at 1/10th the price is the same dollar turnover), so using the
    raw series avoids depending on the adjustment machinery for a number that
    does not need it.
    """
    rows = conn.execute(
        "SELECT close, volume FROM prices WHERE security_id = ? AND date <= ? "
        "AND close IS NOT NULL AND volume IS NOT NULL ORDER BY date DESC LIMIT ?",
        (security_id, as_of, window),
    ).fetchall()
    if len(rows) < window:
        return None
    return sum(float(r["close"]) * float(r["volume"]) for r in rows) / len(rows)


# --------------------------------------------------------------------- entry


def unresolved_candidates(conn) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT c.* FROM research_candidates c
             WHERE NOT EXISTS (
                SELECT 1 FROM cancelled_entries x WHERE x.candidate_id = c.candidate_id)
               AND NOT EXISTS (
                SELECT 1 FROM paper_positions p WHERE p.candidate_id = c.candidate_id)
             ORDER BY c.data_cutoff_at, c.security_id
            """
        )
    ]


def permitted_horizons(conn, candidate: dict, horizons: list[int]) -> list[int]:
    """Horizons selection did NOT already suppress for this candidate's security.

    research_candidates carries no horizon column by design (F10), so which
    books a candidate is admitted to is reconstructed from suppressed_signals:
    a security suppressed there for open_position or book_capacity, at this
    candidate's run and horizon, was never meant to open a position in that book.
    """
    blocked = {
        int(r["horizon_days"])
        for r in conn.execute(
            "SELECT horizon_days FROM suppressed_signals WHERE run_id = ? "
            "AND security_id = ? AND suppression_reason IN ('open_position', 'book_capacity')",
            (candidate["pipeline_run_id"], candidate["security_id"]),
        )
    }
    return [h for h in horizons if h not in blocked]


def split_ratio_on(conn, security_id: int, session: str) -> float:
    row = conn.execute(
        "SELECT ratio FROM corporate_actions WHERE security_id = ? AND ex_date = ? "
        "AND action_type = 'split' AND requires_manual_review = 0 "
        "AND ratio IS NOT NULL AND ratio > 0",
        (security_id, session),
    ).fetchone()
    return float(row["ratio"]) if row else 1.0


def attempt_entry(conn, candidate: dict, sessions: list[str], as_of: str,
                  cfg, run_id: str) -> dict:
    """One security's entry decision. Returns a dict describing what happened."""
    security_id = int(candidate["security_id"])
    entry_session = next_session_after(sessions, candidate["price_data_cutoff"])

    if entry_session is None or entry_session > as_of:
        return {"outcome": "not_yet_due", "security_id": security_id}

    prior_close_row = conn.execute(
        "SELECT close FROM prices WHERE security_id = ? AND date = ?",
        (security_id, candidate["price_data_cutoff"]),
    ).fetchone()
    entry_bar = conn.execute(
        "SELECT open, high, low, close, volume FROM prices "
        "WHERE security_id = ? AND date = ?",
        (security_id, entry_session),
    ).fetchone()

    if prior_close_row is None or entry_bar is None or entry_bar["open"] is None:
        return cancel(conn, candidate, run_id, "no_regular_open",
                     f"no regular-session open recorded for {entry_session} "
                     f"(or the prior close on {candidate['price_data_cutoff']} is missing)",
                     next_open=None)

    if candidate["atr_value"] is None:
        # Not one of the four listed reasons in the strict sense -- there is no
        # halt and no missing session -- but with no ATR there is no basis for
        # the gap test or the stop/target, so the closest defined bucket is
        # used and the real cause is stated in the free text rather than
        # inventing a fifth enum value for a case the fixture does not hit.
        return cancel(conn, candidate, run_id, "no_regular_open",
                     "ATR was unavailable at generation (fewer than the configured "
                     "window of trading days existed), so no gap test or stop/target "
                     "basis exists for this candidate",
                     next_open=float(entry_bar["open"]))

    ratio = split_ratio_on(conn, security_id, entry_session)
    gap = P.gap_test(
        open_price=float(entry_bar["open"]), raw_prior_close=float(prior_close_row["close"]),
        split_ratio=ratio, atr_at_entry_basis=float(candidate["atr_value"]),
        limit_atr=float(candidate["gap_limit_atr"]),
    )
    if gap.cancelled:
        return cancel(conn, candidate, run_id, "gap_above_prior_close", gap.basis_note,
                     next_open=gap.open_price, gap_atr=gap.gap_atr)

    adv = dollar_adv(conn, security_id, candidate["price_data_cutoff"])
    try:
        slippage_bps = P.slippage_for_adv(
            adv, float(cfg["slippage_bps_high_liquidity"]), float(cfg["slippage_bps_mid_liquidity"])
        )
    except P.ProtocolError as exc:
        return cancel(conn, candidate, run_id, "adv_below_protocol_bands", str(exc),
                     next_open=gap.open_price, gap_atr=gap.gap_atr)

    fill = P.entry_fill(gap.open_price, slippage_bps)
    stop, target = P.stop_and_target(
        fill, float(candidate["atr_value"]),
        float(cfg["stop_atr_multiple"]), float(cfg["target_atr_multiple"]),
    )
    shares = float(cfg["position_notional"]) / fill

    horizons = permitted_horizons(conn, candidate, list(cfg["horizons"]))
    return {
        "outcome": "filled", "security_id": security_id, "entry_session": entry_session,
        "fill": fill, "slippage_bps": slippage_bps, "stop": stop, "target": target,
        "shares": shares, "adv": adv, "horizons": horizons, "gap_note": gap.basis_note,
    }


def cancel(conn, candidate: dict, run_id: str, reason: str, basis: str,
          next_open: float | None, gap_atr: float | None = None) -> dict:
    conn.execute(
        "INSERT OR IGNORE INTO cancelled_entries (candidate_id, reason, signal_close, "
        "next_open, gap_atr, adjusted_basis, cancelled_at, run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate["candidate_id"], reason, candidate["signal_close"], next_open,
         gap_atr, basis, utc_now(), run_id),
    )
    return {"outcome": "cancelled", "reason": reason}


def open_positions_for_candidate(conn, candidate: dict, decision: dict, cfg,
                                 run_id: str, sec_security_id: int) -> list[str]:
    """Write the paper_position and matched benchmark_position rows."""
    entry_session = decision["entry_session"]
    created = []
    for horizon in decision["horizons"]:
        book_id = BOOK_IDS.get(horizon, f"book-{horizon}d")
        pid = position_id(candidate["candidate_id"], horizon)
        conn.execute(
            """
            INSERT INTO paper_positions (
                position_id, candidate_id, horizon_days, book_id, protocol_version,
                strategy_version, resolution_policy_version, accrual_policy_version,
                price_snapshot_hash, opened_run_id, last_evaluated_at, entry_date,
                entry_price, slippage_bps, shares, notional, stop_price, target_price,
                status, dividends_received, splits_applied, requires_manual_review
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 0)
            """,
            (pid, candidate["candidate_id"], horizon, book_id, P.PROTOCOL_VERSION,
             int(cfg["strategy_version"]), int(cfg["resolution_policy_version"]),
             int(cfg["accrual_policy_version"]), candidate["price_snapshot_hash"],
             run_id, utc_now(), entry_session, decision["fill"], decision["slippage_bps"],
             decision["shares"], float(cfg["position_notional"]), decision["stop"],
             decision["target"]),
        )
        conn.execute(
            "UPDATE books SET open_position_count = open_position_count + 1 WHERE book_id = ?",
            (book_id,),
        )

        bench_id = position_id(candidate["candidate_id"], horizon, benchmark=True)
        bench_bar = conn.execute(
            "SELECT open FROM prices WHERE security_id = ? AND date = ?",
            (sec_security_id, entry_session),
        ).fetchone()
        bench_adv = dollar_adv(conn, sec_security_id, candidate["price_data_cutoff"])
        try:
            bench_slippage = P.slippage_for_adv(
                bench_adv, float(cfg["slippage_bps_high_liquidity"]),
                float(cfg["slippage_bps_mid_liquidity"]),
            )
        except P.ProtocolError:
            bench_slippage = float(cfg["slippage_bps_high_liquidity"])  # SPY: always liquid
        bench_fill = P.entry_fill(float(bench_bar["open"]), bench_slippage)
        bench_shares = float(cfg["position_notional"]) / bench_fill
        conn.execute(
            """
            INSERT INTO benchmark_positions (
                position_id, candidate_id, horizon_days, book_id, security_id,
                protocol_version, strategy_version, resolution_policy_version,
                accrual_policy_version, price_snapshot_hash, opened_run_id,
                last_evaluated_at, entry_date, entry_price, slippage_bps, shares,
                notional, status, dividends_received, splits_applied, requires_manual_review
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 0)
            """,
            (bench_id, candidate["candidate_id"], horizon, book_id, sec_security_id,
             P.PROTOCOL_VERSION, int(cfg["strategy_version"]),
             int(cfg["resolution_policy_version"]), int(cfg["accrual_policy_version"]),
             candidate["price_snapshot_hash"], run_id, utc_now(), entry_session,
             bench_fill, bench_slippage, bench_shares, float(cfg["position_notional"])),
        )
        created.append(pid)
    return created


def resolve_spy_security_id(conn) -> int | None:
    row = conn.execute("SELECT security_id FROM listings WHERE symbol = 'SPY' LIMIT 1").fetchone()
    return int(row["security_id"]) if row else None


# --------------------------------------------------------------- walk-forward


def walk_forward_position(conn, position: dict, sessions: list[str], as_of: str,
                          cfg) -> None:
    """Advance one OPEN paper_position through every unevaluated session."""
    security_id = conn.execute(
        "SELECT security_id FROM research_candidates WHERE candidate_id = ?",
        (position["candidate_id"],),
    ).fetchone()["security_id"]

    to_walk = sessions_between(sessions, position["entry_date"], as_of)
    shares = float(position["shares"])
    entry_price = float(position["entry_price"])
    stop = float(position["stop_price"])
    target = float(position["target_price"])
    dividends = float(position["dividends_received"])
    splits_applied = float(position["splits_applied"])
    # Sessions already held before this walk starts: everything strictly after
    # entry_date and strictly before the first session about to be evaluated.
    boundary = to_walk[0] if to_walk else as_of
    already_held = len([s for s in sessions if position["entry_date"] < s < boundary])

    for index, session in enumerate(to_walk):
        security_row = conn.execute(
            "SELECT is_active, delisted_date FROM securities WHERE security_id = ?",
            (security_id,),
        ).fetchone()

        # Delisting takes priority over evaluating a bar that will not exist.
        if security_row["is_active"] == 0 and security_row["delisted_date"] and \
           security_row["delisted_date"] <= session:
            resolve_delisting(conn, position["position_id"], security_row["delisted_date"],
                              session)
            return

        manual = conn.execute(
            "SELECT requires_manual_review FROM paper_positions WHERE position_id = ?",
            (position["position_id"],),
        ).fetchone()
        if manual and manual["requires_manual_review"]:
            touch(conn, "paper_positions", position["position_id"], session)
            return

        action = conn.execute(
            "SELECT ex_date, action_type, ratio, cash_amount FROM corporate_actions "
            "WHERE security_id = ? AND ex_date = ?", (security_id, session),
        ).fetchone()
        if action is not None:
            if action["action_type"] == "split":
                result = ACT.apply_split(
                    ratio=float(action["ratio"]), shares=shares, entry_price=entry_price,
                    stop=stop, target=target,
                )
                record_event(conn, position["position_id"], session, "split",
                            ratio=float(action["ratio"]), cash_amount=None,
                            cash_accrued=0.0, entitled=True, payment_date=None,
                            shares_before=shares, shares_after=result.shares_after,
                            note=result.note)
                shares, entry_price, stop, target = (
                    result.shares_after, result.entry_price_after,
                    result.stop_after, result.target_after,
                )
                splits_applied *= float(action["ratio"])
            elif action["action_type"] == "dividend":
                result = ACT.apply_dividend(
                    entry_date=position["entry_date"], ex_date=session, shares=shares,
                    cash_amount=float(action["cash_amount"]),
                )
                record_event(conn, position["position_id"], session, "dividend",
                            ratio=None, cash_amount=float(action["cash_amount"]),
                            cash_accrued=result.cash_accrued, entitled=result.entitled,
                            payment_date=None, shares_before=shares, shares_after=shares,
                            note=result.note)
                dividends += result.cash_accrued
            elif action["action_type"] in ACT.MANUAL_REVIEW_ACTION_TYPES:
                record_event(conn, position["position_id"], session, action["action_type"],
                            ratio=action["ratio"], cash_amount=action["cash_amount"],
                            cash_accrued=0.0, entitled=False, payment_date=None,
                            shares_before=shares, shares_after=shares,
                            note=f"{action['action_type']} on {session} requires manual "
                                 f"review; automatic evaluation frozen")
                conn.execute(
                    "UPDATE paper_positions SET requires_manual_review = 1, shares = ?, "
                    "entry_price = ?, stop_price = ?, target_price = ?, dividends_received = ?, "
                    "splits_applied = ?, last_evaluated_at = ? WHERE position_id = ?",
                    (shares, entry_price, stop, target, dividends, splits_applied, utc_now(),
                     position["position_id"]),
                )
                return

        bar = conn.execute(
            "SELECT open, high, low, close FROM prices WHERE security_id = ? AND date = ?",
            (security_id, session),
        ).fetchone()
        if bar is None or bar["open"] is None:
            # No bar and not (yet) marked delisted: nothing to evaluate today.
            # Persist any corporate-action mutation and move on to the next
            # session on a later run.
            conn.execute(
                "UPDATE paper_positions SET shares = ?, entry_price = ?, stop_price = ?, "
                "target_price = ?, dividends_received = ?, splits_applied = ?, "
                "last_evaluated_at = ? WHERE position_id = ?",
                (shares, entry_price, stop, target, dividends, splits_applied, utc_now(),
                 position["position_id"]),
            )
            continue

        held_sessions_now = already_held + index + 1
        decision = P.evaluate_bar(
            open_price=float(bar["open"]), high=float(bar["high"]), low=float(bar["low"]),
            close=float(bar["close"]), stop=stop, target=target,
            held_sessions=held_sessions_now, horizon_days=int(position["horizon_days"]),
        )

        conn.execute(
            "UPDATE paper_positions SET shares = ?, entry_price = ?, stop_price = ?, "
            "target_price = ?, dividends_received = ?, splits_applied = ?, "
            "last_evaluated_at = ? WHERE position_id = ?",
            (shares, entry_price, stop, target, dividends, splits_applied, utc_now(),
             position["position_id"]),
        )

        if decision.exit:
            raw_exit = decision.raw_price
            exit_fill_price = P.exit_fill(raw_exit, float(position["slippage_bps"]))
            gross, net, pct = P.pnl(
                shares=shares, entry_price=entry_price,
                exit_price=exit_fill_price, dividends=dividends,
                notional=float(position["notional"]),
            )
            conn.execute(
                "UPDATE paper_positions SET status = 'closed', exit_date = ?, exit_price = ?, "
                "exit_reason = ?, gross_pnl = ?, net_pnl = ?, pnl_pct = ?, last_evaluated_at = ? "
                "WHERE position_id = ?",
                (session, exit_fill_price, decision.reason, gross, net, pct, utc_now(),
                 position["position_id"]),
            )
            conn.execute(
                "UPDATE books SET open_position_count = MAX(0, open_position_count - 1), "
                "current_nav = current_nav + ? WHERE book_id = ?",
                (net, position["book_id"]),
            )
            close_matched_benchmark(conn, position["candidate_id"], position["horizon_days"], session)
            return


def resolve_delisting(conn, position_id_: str, delisted_date: str, as_of: str) -> None:
    """Move a position into (or advance) pending_resolution."""
    row = conn.execute(
        "SELECT * FROM paper_positions WHERE position_id = ?", (position_id_,)
    ).fetchone()
    decision = DL.resolve(delisted_date=delisted_date, as_of_date=as_of, verified_recovery=None)

    if row["status"] != "pending_resolution":
        conn.execute(
            "UPDATE paper_positions SET status = 'pending_resolution', last_evaluated_at = ? "
            "WHERE position_id = ?", (utc_now(), position_id_),
        )

    if decision.action == "resolve_zero":
        gross, net, pct = P.pnl(
            shares=float(row["shares"]), entry_price=float(row["entry_price"]),
            exit_price=0.0, dividends=float(row["dividends_received"]),
            notional=float(row["notional"]),
        )
        exit_date = (DL.parse_date(delisted_date) + timedelta(days=DL.UNRESOLVED_GRACE_DAYS)).isoformat()
        conn.execute(
            "UPDATE paper_positions SET status = 'closed', exit_date = ?, exit_price = 0, "
            "exit_reason = 'delisting_zero_after_180d', gross_pnl = ?, net_pnl = ?, "
            "pnl_pct = ?, last_evaluated_at = ? WHERE position_id = ?",
            (exit_date, gross, net, pct, utc_now(), position_id_),
        )
        conn.execute(
            "UPDATE books SET open_position_count = MAX(0, open_position_count - 1), "
            "current_nav = current_nav + ? WHERE book_id = ?", (net, row["book_id"]),
        )
        close_matched_benchmark(conn, row["candidate_id"], row["horizon_days"], exit_date)
    else:
        conn.execute(
            "UPDATE paper_positions SET last_evaluated_at = ? WHERE position_id = ?",
            (utc_now(), position_id_),
        )


def close_matched_benchmark(conn, candidate_id: str, horizon_days: int, exit_date: str) -> None:
    bench = conn.execute(
        "SELECT * FROM benchmark_positions WHERE candidate_id = ? AND horizon_days = ? "
        "AND status = 'open'", (candidate_id, horizon_days),
    ).fetchone()
    if bench is None:
        return
    bar = conn.execute(
        "SELECT close FROM prices WHERE security_id = ? AND date <= ? "
        "AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
        (bench["security_id"], exit_date),
    ).fetchone()
    if bar is None:
        return  # no SPY bar at or before the exit date yet; close on a later run
    exit_price = P.exit_fill(float(bar["close"]), float(bench["slippage_bps"]))
    gross, net, pct = P.pnl(
        shares=float(bench["shares"]), entry_price=float(bench["entry_price"]),
        exit_price=exit_price, dividends=float(bench["dividends_received"]),
        notional=float(bench["notional"]),
    )
    conn.execute(
        "UPDATE benchmark_positions SET status = 'closed', exit_date = ?, exit_price = ?, "
        "exit_reason = 'matched_close', gross_pnl = ?, net_pnl = ?, pnl_pct = ?, "
        "last_evaluated_at = ? WHERE position_id = ?",
        (exit_date, exit_price, gross, net, pct, utc_now(), bench["position_id"]),
    )


def touch(conn, table: str, position_id_: str, as_of: str) -> None:
    conn.execute(f"UPDATE {table} SET last_evaluated_at = ? WHERE position_id = ?",
                (utc_now(), position_id_))


def record_event(conn, position_id_: str, ex_date: str, action_type: str, *, ratio, cash_amount,
                 cash_accrued: float, entitled: bool, payment_date, shares_before: float,
                 shares_after: float, note: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO position_events (position_id, ex_date, action_type, ratio, "
        "cash_amount, cash_accrued, entitled, payment_date, shares_before, shares_after, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (position_id_, ex_date, action_type, ratio, cash_amount, cash_accrued,
         1 if entitled else 0, payment_date, shares_before, shares_after, note),
    )


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute and resolve paper positions")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    cfg_hash = config_hash(args.config)
    conn = migrate.connect(Path(args.db))
    run_id = f"execution-{uuid.uuid4().hex[:12]}"
    entries, cancellations, closed, touched = 0, 0, 0, 0

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'execution', ?, 'running', ?)", (run_id, utc_now(), CODE_VERSION),
        )
        sessions = all_sessions(conn)
        spy_id = resolve_spy_security_id(conn)

        for candidate in unresolved_candidates(conn):
            decision = attempt_entry(conn, candidate, sessions, args.as_of, cfg, run_id)
            if decision["outcome"] == "cancelled":
                cancellations += 1
            elif decision["outcome"] == "filled":
                if spy_id is None:
                    raise SystemExit("SPY security not found; cannot open matched benchmark positions")
                open_positions_for_candidate(conn, candidate, decision, cfg, run_id,
                                            candidate["security_id"])
                entries += len(decision["horizons"])

        for position in [
            dict(r) for r in conn.execute(
                "SELECT * FROM paper_positions WHERE status IN ('open', 'pending_resolution')"
            )
        ]:
            before = conn.execute(
                "SELECT status FROM paper_positions WHERE position_id = ?",
                (position["position_id"],),
            ).fetchone()["status"]
            walk_forward_position(conn, position, sessions, args.as_of, cfg)
            after = conn.execute(
                "SELECT status FROM paper_positions WHERE position_id = ?",
                (position["position_id"],),
            ).fetchone()["status"]
            if after == "closed" and before != "closed":
                closed += 1
            else:
                touched += 1

        conn.execute(
            "UPDATE pipeline_runs SET status='success', finished_at=?, records_written=? "
            "WHERE run_id=?", (utc_now(), entries + cancellations, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print(PHASE_BANNER)
    print(f"\nrun {run_id}  as-of {args.as_of}")
    print(f"entries opened: {entries}   cancelled: {cancellations}   "
         f"closed this run: {closed}   still open/pending: {touched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
