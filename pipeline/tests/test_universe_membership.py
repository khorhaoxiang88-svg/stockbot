"""S1 universe rules: entry, retention, hysteresis, monitoring continuation."""

import sqlite3

import pytest

import migrate
from config_loader import load_config
from universe import identity, membership as M


@pytest.fixture
def conn(tmp_path):
    connection = migrate.connect(tmp_path / "universe_membership.db")
    migrate.migrate_up(connection)
    yield connection
    connection.close()


@pytest.fixture
def config():
    return dict(load_config())


AS_OF = "2026-08-01"


PRICE_SERIES_START = "2025-01-01"
PRICE_SERIES_END = "2026-08-01"


def seed_security(
    conn,
    *,
    cik="0000000900",
    exchange="NYSE",
    security_type="common_stock",
    confidence="high",
    price=50.0,
    volume=200_000,
    market_cap=500_000_000.0,
    market_cap_confidence="high",
    quarters=8,
    files_reports=True,
    price_days=None,
    as_of=AS_OF,
) -> int:
    """One security with everything gather_metrics needs: a listing, a
    continuous daily price series from PRICE_SERIES_START to PRICE_SERIES_END
    (so any as_of used across these tests, which all fall in 2026, has 60+
    trailing days available), a derived_fundamentals row for market cap known
    well before any of those dates, a 10-K filing, and `quarters` consecutive
    quarterly XBRL facts. Every knob defaults to a value that clears every
    entry threshold, so a test only needs to override what it's actually
    testing. `price_days`, if given, seeds only that many trailing days
    instead of the full series -- used to test "no price data on record".
    """
    security_id = identity.create_security(
        conn,
        name="Test Co",
        cik=cik,
        security_type=security_type,
        classification_confidence=confidence,
        classification_source="test",
        first_seen="2026-01-01T00:00:00Z",
        last_seen="2026-01-01T00:00:00Z",
    )
    identity.add_listing(
        conn, security_id=security_id, symbol=f"T{security_id}", exchange=exchange,
        valid_from="2020-01-01",
    )

    if price_days != 0:
        from datetime import date, timedelta

        conn.execute(
            "INSERT OR IGNORE INTO price_dataset_versions "
            "(dataset_version, created_at, provider, reason, changed_row_count) "
            "VALUES (1, ?, 'test', 'seed', 0)",
            (as_of + "T00:00:00Z",),
        )
        end = date.fromisoformat(PRICE_SERIES_END)
        total_days = (end - date.fromisoformat(PRICE_SERIES_START)).days + 1
        span = price_days if price_days is not None else total_days
        now = as_of + "T00:00:00Z"
        rows = [
            (security_id, (end - timedelta(days=i)).isoformat(), price, price, price, price,
             volume, now, now)
            for i in range(span)
        ]
        conn.executemany(
            "INSERT INTO prices (security_id, date, open, high, low, close, volume, "
            "provider, first_seen_at, last_verified_at, revision, price_data_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'test', ?, ?, 0, 1)",
            rows,
        )

    if market_cap is not None:
        # Known well before any as_of used in these tests (all >= 2026-01-01).
        conn.execute(
            "INSERT INTO derived_fundamentals (security_id, period_end, knowledge_date, "
            "fact_set_hash, mapping_version, market_cap, market_cap_confidence, "
            "inputs_complete, computed_at) VALUES (?, '2025-09-30', '2025-10-15T00:00:00Z', "
            "'hash', 1, ?, ?, 1, '2025-10-15T00:00:00Z')",
            (security_id, market_cap, market_cap_confidence),
        )

    if files_reports:
        conn.execute(
            "INSERT INTO filings (accession_no, cik, form_type, filed_date, accepted_at) "
            "VALUES (?, ?, '10-K', '2026-02-01', '2026-02-01T00:00:00Z')",
            (f"acc-{security_id}-10k", cik),
        )

    if quarters:
        conn.execute(
            "INSERT INTO raw_payloads (payload_id, source, endpoint, identifier, relative_path, "
            "content_hash, byte_size, fetched_at) VALUES (?, 'sec', 'companyfacts', ?, 'x', 'h', 1, ?)",
            (f"payload-{security_id}", f"CIK{cik}", as_of + "T00:00:00Z"),
        )
        from datetime import date, timedelta

        end = date.fromisoformat("2025-12-31")
        for q in range(quarters):
            q_end = end - timedelta(days=91 * q)
            q_start = q_end - timedelta(days=90)
            conn.execute(
                "INSERT INTO xbrl_facts (payload_id, source_fact_key, cik, taxonomy, concept, "
                "unit, context_type, period_start, period_end, context_hash, semantic_hash, "
                "accepted_at, source_endpoint) VALUES (?, ?, ?, 'us-gaap', 'Revenues', 'USD', "
                "'duration', ?, ?, ?, ?, ?, 'companyfacts')",
                (
                    f"payload-{security_id}", f"key-{security_id}-{q}", cik,
                    q_start.isoformat(), q_end.isoformat(), f"ctx-{security_id}-{q}",
                    f"sem-{security_id}-{q}", as_of + "T00:00:00Z",
                ),
            )

    return security_id


def security_dict(conn, security_id):
    row = conn.execute(
        "SELECT security_id, cik, security_type, classification_confidence "
        "FROM securities WHERE security_id = ?", (security_id,),
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------- entry rules


def test_a_clean_security_clears_every_entry_rule(conn, config):
    sid = seed_security(conn)
    metrics = M.gather_metrics(conn, security_dict(conn, sid), AS_OF, config)
    assert M.entry_exclusion_reason(metrics, config) is None


@pytest.mark.parametrize(
    "overrides,expected_substring",
    [
        ({"exchange": "NYSE Arca"}, "not listed on NYSE, Nasdaq or NYSE American"),
        ({"security_type": "preferred_share"}, "preferred share"),
        ({"security_type": "etf"}, "ETF"),
        ({"security_type": "warrant"}, "warrant"),
        ({"confidence": "low"}, "classification confidence"),
        ({"files_reports": False}, "no 10-K or 10-Q"),
        ({"price": 1.50}, "below the $3.00 entry minimum"),
        ({"market_cap": 100_000_000.0}, "below the $300,000,000 entry minimum"),
        ({"volume": 1000}, "below the $5,000,000 entry minimum"),
        ({"quarters": 3}, "below the 8-quarter entry minimum"),
    ],
)
def test_each_entry_rule_excludes_with_its_own_reason(conn, config, overrides, expected_substring):
    sid = seed_security(conn, **overrides)
    metrics = M.gather_metrics(conn, security_dict(conn, sid), AS_OF, config)
    reason = M.entry_exclusion_reason(metrics, config)
    assert reason is not None
    assert expected_substring in reason


def test_annual_fact_bridges_the_implicit_q4_gap(conn, config):
    """Regression: discovered against real ingested data for RCUS and FERG.

    SEC filers tag Q1-Q3 as explicit ~90-day duration facts (10-Qs) but never
    tag a standalone Q4 duration -- the 10-K reports the full fiscal year.
    Without treating the annual fact's period_end as a Q4 checkpoint, every
    filer shows a false ~6-month gap once a year and gets undercounted.
    """
    sid = seed_security(conn, quarters=0)  # build the fact history by hand
    cik = "0000000900"
    conn.execute(
        "INSERT INTO raw_payloads (payload_id, source, endpoint, identifier, relative_path, "
        "content_hash, byte_size, fetched_at) VALUES ('payload-q4', 'sec', 'companyfacts', "
        "?, 'x', 'h', 1, ?)",
        (f"CIK{cik}", AS_OF + "T00:00:00Z"),
    )
    # Two fiscal years of the REAL shape: Q1, Q2, Q3 explicit, Q4 only as the
    # implicit tail of the annual (FY) fact -- 8 quarters of true coverage,
    # only 6 of them individually tagged.
    facts = [
        ("2024-01-01", "2024-03-31"),  # Q1 2024
        ("2024-04-01", "2024-06-30"),  # Q2 2024
        ("2024-07-01", "2024-09-30"),  # Q3 2024
        ("2024-01-01", "2024-12-31"),  # FY2024 (10-K) -- bridges Q4 2024
        ("2025-01-01", "2025-03-31"),  # Q1 2025
        ("2025-04-01", "2025-06-30"),  # Q2 2025
        ("2025-07-01", "2025-09-30"),  # Q3 2025
        ("2025-01-01", "2025-12-31"),  # FY2025 (10-K) -- bridges Q4 2025
    ]
    for i, (start, end) in enumerate(facts):
        conn.execute(
            "INSERT INTO xbrl_facts (payload_id, source_fact_key, cik, taxonomy, concept, "
            "unit, context_type, period_start, period_end, context_hash, semantic_hash, "
            "accepted_at, source_endpoint) VALUES ('payload-q4', ?, ?, 'us-gaap', 'Revenues', "
            "'USD', 'duration', ?, ?, ?, ?, ?, 'companyfacts')",
            (f"q4key-{i}", cik, start, end, f"q4ctx-{i}", f"q4sem-{i}", AS_OF + "T00:00:00Z"),
        )

    streak = M.xbrl_consecutive_quarters(conn, cik, "2026-01-01")
    assert streak == 8, (
        f"expected the annual facts to bridge both implicit Q4s for a full 8-quarter "
        f"streak, got {streak}"
    )


def test_no_cik_is_excluded_with_a_reason(conn, config):
    security_id = identity.create_security(
        conn, name="No CIK Co", cik=None, security_type="common_stock",
        classification_confidence="high", classification_source="test",
        first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-01T00:00:00Z",
    )
    identity.add_listing(conn, security_id=security_id, symbol="NOCIK", exchange="NYSE", valid_from="2020-01-01")
    metrics = M.gather_metrics(conn, security_dict(conn, security_id), AS_OF, config)
    assert metrics["cik"] is None
    assert M.entry_exclusion_reason(metrics, config) == "no CIK on record"


# ------------------------------------------------------------ retention rules


def test_retention_thresholds_are_strictly_below_entry_thresholds(config):
    pairs = [
        ("universe_retention_price_min", "universe_entry_price_min"),
        ("universe_retention_market_cap_min", "universe_entry_market_cap_min"),
        ("universe_retention_adv_min", "universe_entry_adv_min"),
    ]
    for retention_key, entry_key in pairs:
        assert config[retention_key] < config[entry_key], (
            f"{retention_key} ({config[retention_key]}) must be strictly below "
            f"{entry_key} ({config[entry_key]})"
        )


def test_below_retention_true_only_below_the_lower_bar(conn, config):
    # volume high enough that ADV clears retention even at this low a price --
    # this test isolates the PRICE boundary, not ADV.
    sid = seed_security(conn, price=2.75, market_cap=500_000_000.0, volume=2_000_000)
    metrics = M.gather_metrics(conn, security_dict(conn, sid), AS_OF, config)
    # 2.75 is below entry (3.00) but above retention (2.50): NOT below retention.
    assert metrics["price"] == 2.75
    assert not M.below_retention(metrics, config)


def test_below_retention_true_when_actually_below_the_lower_bar(conn, config):
    sid = seed_security(conn, price=2.00)
    metrics = M.gather_metrics(conn, security_dict(conn, sid), AS_OF, config)
    assert M.below_retention(metrics, config)


# --------------------------------------------------------- exclusion evidence


def test_excluded_security_always_carries_a_non_empty_reason(conn, config):
    sid = seed_security(conn, price=0.50)
    snapshot_id = M.compute_snapshot(conn, AS_OF, "monthly_membership", pool_versions=[], extra_security_ids=[sid])
    row = conn.execute(
        "SELECT status, exclusion_reason FROM universe_snapshots "
        "WHERE snapshot_id = ? AND security_id = ?", (snapshot_id, sid),
    ).fetchone()
    assert row["status"] == "excluded"
    assert row["exclusion_reason"] and row["exclusion_reason"].strip()


def test_watch_status_also_carries_a_reason_enforced_by_the_db(conn):
    """Migration 016 strengthened the CHECK so 'watch' needs a reason too."""
    snapshot_id = "snap-check-test"
    conn.execute(
        "INSERT INTO universe_snapshot_runs (snapshot_id, effective_at, rules_version, "
        "config_hash, run_id, security_count, is_official, run_type) "
        "VALUES (?, ?, 'v1', 'hash', NULL, 0, 0, 'daily_safety')",
        (snapshot_id, AS_OF + "T00:00:00Z"),
    )
    sid = seed_security(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO universe_snapshots (snapshot_id, security_id, snapshot_date, status, "
            "exclusion_reason) VALUES (?, ?, ?, 'watch', NULL)",
            (snapshot_id, sid, AS_OF),
        )


# ------------------------------------------------------------------ oscillation


def test_oscillating_around_the_entry_threshold_does_not_flap_in_and_out(conn, config):
    """A security that crosses the entry price line back and forth every
    month, but never drops below the (much lower) retention price, enters
    once and stays in -- it is never re-judged against the entry bar again
    once included."""
    # volume high enough that dollar ADV clears entry (and stays above
    # retention) across every wobble price used below, so this test isolates
    # the PRICE oscillation, not ADV.
    sid = seed_security(conn, price=3.50, volume=2_000_000)

    snap1 = M.compute_snapshot(conn, "2026-01-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])
    status1 = conn.execute(
        "SELECT status FROM universe_snapshots WHERE snapshot_id = ? AND security_id = ?",
        (snap1, sid),
    ).fetchone()["status"]
    assert status1 == "included"

    # Dips below entry (3.00) but stays above retention (2.50) for several
    # consecutive monthly cycles.
    for i, wobble_price in enumerate([2.80, 3.10, 2.60, 3.20, 2.90]):
        conn.execute("UPDATE prices SET close = ? WHERE security_id = ?", (wobble_price, sid))
        snap = M.compute_snapshot(
            conn, f"2026-0{2 + i}-01", "monthly_membership", pool_versions=[]
        )
        status = conn.execute(
            "SELECT status FROM universe_snapshots WHERE snapshot_id = ? AND security_id = ?",
            (snap, sid),
        ).fetchone()["status"]
        assert status == "included", (
            f"wobble to {wobble_price} (below entry, above retention) must not exit"
        )

    changes = conn.execute(
        "SELECT change_type FROM universe_membership_changes WHERE security_id = ? ORDER BY recorded_at",
        (sid,),
    ).fetchall()
    assert [c["change_type"] for c in changes] == ["entered"], (
        "must enter exactly once and never exit from entry-threshold oscillation alone"
    )


def test_exit_requires_the_full_hysteresis_window_of_daily_checks(conn, config):
    sid = seed_security(conn, price=50.0)
    snap0 = M.compute_snapshot(conn, "2026-01-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])
    assert conn.execute(
        "SELECT status FROM universe_snapshots WHERE snapshot_id = ? AND security_id = ?",
        (snap0, sid),
    ).fetchone()["status"] == "included"

    # Drop below retention and run daily_safety checks for fewer days than
    # the configured hysteresis window.
    conn.execute("UPDATE prices SET close = 2.00 WHERE security_id = ?", (sid,))
    threshold = config["universe_retention_hysteresis_days"]
    for day in range(threshold - 1):
        M.compute_snapshot(conn, f"2026-02-{day + 1:02d}", "daily_safety", pool_versions=[], extra_security_ids=[sid])

    snap_short = M.compute_snapshot(conn, "2026-03-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])
    status_short = conn.execute(
        "SELECT status FROM universe_snapshots WHERE snapshot_id = ? AND security_id = ?",
        (snap_short, sid),
    ).fetchone()["status"]
    assert status_short == "included", "must not exit before the hysteresis window completes"

    # One more daily check reaches the threshold.
    M.compute_snapshot(conn, "2026-03-02", "daily_safety", pool_versions=[], extra_security_ids=[sid])
    snap_final = M.compute_snapshot(conn, "2026-04-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])
    status_final = conn.execute(
        "SELECT status FROM universe_snapshots WHERE snapshot_id = ? AND security_id = ?",
        (snap_final, sid),
    ).fetchone()["status"]
    assert status_final == "excluded"

    changes = conn.execute(
        "SELECT change_type FROM universe_membership_changes WHERE security_id = ? ORDER BY recorded_at",
        (sid,),
    ).fetchall()
    assert [c["change_type"] for c in changes] == ["entered", "exited"]


# ---------------------------------------------------------- continued monitoring


def test_a_security_that_exits_with_an_open_position_is_still_monitored(conn, config):
    sid = seed_security(conn, price=50.0)
    snap0 = M.compute_snapshot(conn, "2026-01-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])

    conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, started_at, status) "
        "VALUES ('run-1', 'test', '2026-01-01T00:00:00Z', 'success')"
    )
    conn.execute(
        "INSERT INTO books (book_id, horizon_days, starting_nav, current_nav, strategy_version) "
        "VALUES ('book-20', 20, 100000, 100000, 1)"
    )

    # Give it an open paper position, then drive it out of the universe.
    conn.execute(
        "INSERT INTO research_candidates (candidate_id, security_id, generated_at, "
        "data_cutoff_at, snapshot_id, pipeline_run_id, strategy_version, config_hash, "
        "code_version, selection_rule_version, mapping_version, price_dataset_version, "
        "price_snapshot_hash, source_health_snapshot_json, score_snapshot_json, "
        "accessions_used_json, composite_at_generation, rank_at_generation, signal_close, "
        "atr_value, atr_window, price_data_cutoff, entry_rule, gap_limit_atr, row_hash) VALUES "
        "('cand-1', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', ?, 'run-1', 1, 'h', 'v1', "
        "1, 1, 1, 'h', '{}', '{}', '{}', 80.0, 1, 50.0, 1.0, 14, '2026-01-01', 'open', 1.0, 'rowhash')",
        (sid, snap0),
    )
    conn.execute(
        "INSERT INTO paper_positions (position_id, candidate_id, horizon_days, book_id, "
        "protocol_version, strategy_version, resolution_policy_version, accrual_policy_version, "
        "price_snapshot_hash, opened_run_id, last_evaluated_at, entry_date, entry_price, "
        "slippage_bps, shares, notional, stop_price, target_price, status) VALUES "
        "('pos-1', 'cand-1', 20, 'book-20', 1, 1, 1, 1, 'h', 'run-1', '2026-01-02T00:00:00Z', "
        "'2026-01-02', 50.25, 5, 20, 1000, 42.0, 58.0, 'open')",
    )

    conn.execute("UPDATE prices SET close = 2.00 WHERE security_id = ?", (sid,))
    threshold = config["universe_retention_hysteresis_days"]
    for day in range(threshold):
        M.compute_snapshot(conn, f"2026-02-{day + 1:02d}", "daily_safety", pool_versions=[], extra_security_ids=[sid])
    M.compute_snapshot(conn, "2026-03-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])

    latest_status = conn.execute(
        """
        SELECT sn.status FROM universe_snapshots sn
          JOIN (SELECT security_id, MAX(snapshot_date) d FROM universe_snapshots GROUP BY security_id) l
            ON l.security_id = sn.security_id AND l.d = sn.snapshot_date
         WHERE sn.security_id = ?
        """,
        (sid,),
    ).fetchone()["status"]
    assert latest_status == "excluded", "sanity check: it really did exit the formal universe"

    monitored = M.securities_requiring_monitoring(conn)
    assert sid in monitored, "an open position must keep a security monitored after it exits"


def test_a_security_with_no_open_position_and_no_membership_is_not_monitored(conn):
    sid = seed_security(conn, price=0.50)  # excluded on entry, never included
    M.compute_snapshot(conn, AS_OF, "monthly_membership", pool_versions=[], extra_security_ids=[sid])
    assert sid not in M.securities_requiring_monitoring(conn)


# --------------------------------------------------------- nothing removed retroactively


def test_a_new_snapshot_never_touches_a_prior_ones_rows(conn, config):
    sid = seed_security(conn)
    snap1 = M.compute_snapshot(conn, "2026-01-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])
    row1_before = dict(
        conn.execute(
            "SELECT * FROM universe_snapshots WHERE snapshot_id = ? AND security_id = ?",
            (snap1, sid),
        ).fetchone()
    )

    conn.execute("UPDATE prices SET close = 500.0 WHERE security_id = ?", (sid,))
    M.compute_snapshot(conn, "2026-02-01", "monthly_membership", pool_versions=[], extra_security_ids=[sid])

    row1_after = dict(
        conn.execute(
            "SELECT * FROM universe_snapshots WHERE snapshot_id = ? AND security_id = ?",
            (snap1, sid),
        ).fetchone()
    )
    assert row1_before == row1_after
    assert conn.execute("SELECT COUNT(DISTINCT snapshot_id) FROM universe_snapshots").fetchone()[0] == 2
