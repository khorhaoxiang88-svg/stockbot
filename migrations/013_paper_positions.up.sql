-- 013 up: paper positions, matched benchmark positions, cancelled entries.
--
-- NUMBERING NOTE: the F11 brief called this "migration 011", but 011
-- (selection and books) and 012 (the overdue_issuer_filing flag code) are both
-- already applied. Editing an applied migration is forbidden, so this is 013.
-- The specified column lists are unchanged.
--
-- SUPERSEDES `positions`. F10's brief specified no position table, and one was
-- added there as the minimum needed to express four rules that referenced open
-- positions and cooldowns. F11 specifies the real thing. Keeping both would
-- leave two answers to "is there an open position for this security", so
-- `positions` is dropped here and pipeline/selection reads paper_positions
-- instead. It holds zero rows -- F10 selected no candidates -- so nothing is
-- lost; the DROP is guarded by that fact rather than assuming it.
--
-- Four ideas carry this migration.
--
-- 1. A CANCELLED ENTRY IS A RESULT. A candidate whose entry gapped away was
--    still selected, and hiding it would flatter every statistic that divides
--    by the number of attempts. cancelled_entries records the numbers that
--    produced the decision, including the adjusted basis, so a split can be
--    seen not to have caused it.
--
-- 2. A DELISTING IS NOT AN EXIT. The last quoted price of a security that
--    stopped trading may never have been executable, so nothing here may close
--    a position at it. Status becomes pending_resolution, the position stays in
--    reported exposure, and resolution follows a recorded policy.
--
-- 3. LOSSES ARE STORED IDENTICALLY TO WINS. There is no separate column, flag
--    or status for a losing trade, and no CHECK that treats one differently.
--
-- 4. EVERY POSITION CARRIES ITS PROTOCOL. protocol_version,
--    resolution_policy_version and accrual_policy_version are stamped per row,
--    because a paper record whose rules cannot be reconstructed is not evidence.

PRAGMA foreign_keys = OFF;

DROP INDEX IF EXISTS idx_positions_cooldown;
DROP INDEX IF EXISTS idx_positions_book;
DROP INDEX IF EXISTS idx_positions_one_open_per_security_horizon;
DROP TABLE IF EXISTS positions;

CREATE TABLE IF NOT EXISTS paper_positions (
    position_id   TEXT PRIMARY KEY,
    candidate_id  TEXT NOT NULL REFERENCES research_candidates (candidate_id),
    horizon_days  INTEGER NOT NULL,
    book_id       TEXT NOT NULL REFERENCES books (book_id),

    ------------------------------------------------------------------ provenance
    protocol_version          TEXT NOT NULL,   -- 'R1-PROTOCOL-1.1', frozen
    strategy_version          INTEGER NOT NULL,
    resolution_policy_version INTEGER NOT NULL,
    accrual_policy_version    INTEGER NOT NULL,
    price_snapshot_hash       TEXT,
    opened_run_id             TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    -- Keeps updating while a position is pending_resolution, so a frozen
    -- position is distinguishable from a forgotten one.
    last_evaluated_at         TEXT NOT NULL,   -- UTC

    ----------------------------------------------------------------------- entry
    entry_date    TEXT NOT NULL,               -- ET trading date of the fill
    entry_price   REAL NOT NULL,               -- the FILL, after adverse slippage
    slippage_bps  REAL NOT NULL,
    shares        REAL NOT NULL,
    notional      REAL NOT NULL,
    -- Both computed from the actual fill, never from the signal close, and
    -- divided by the split ratio on any ex-date during the hold.
    stop_price    REAL NOT NULL,
    target_price  REAL NOT NULL,

    ------------------------------------------------------------------------ life
    status        TEXT NOT NULL CHECK (status IN ('open', 'closed', 'pending_resolution')),
    exit_date     TEXT,
    exit_price    REAL,
    exit_reason   TEXT CHECK (exit_reason IS NULL OR exit_reason IN (
                      'stop', 'target',
                      'gap_through_stop', 'gap_through_target',
                      'time_exit',
                      'delisting_resolved_consideration',
                      'delisting_resolved_market',
                      'delisting_zero_after_180d')),

    dividends_received REAL NOT NULL DEFAULT 0,
    -- Cumulative product of split ratios applied during the hold. 1.0 means none.
    splits_applied     REAL NOT NULL DEFAULT 1.0,

    gross_pnl     REAL,
    net_pnl       REAL,
    pnl_pct       REAL,

    -- Set by a spin-off, merger or special distribution. FREEZES automatic
    -- evaluation: the engine will not advance this position again.
    requires_manual_review INTEGER NOT NULL DEFAULT 0
                                CHECK (requires_manual_review IN (0, 1)),

    UNIQUE (candidate_id, horizon_days),

    CHECK (shares > 0),
    CHECK (notional > 0),
    CHECK (entry_price > 0),
    -- Slippage is ADVERSE ON EVERY FILL, with no exceptions. A zero or negative
    -- value would be a free or favourable fill, which the protocol forbids.
    CHECK (slippage_bps > 0),
    CHECK (stop_price > 0 AND stop_price < entry_price),
    CHECK (target_price > entry_price),
    CHECK (splits_applied > 0),
    -- A closed position must say when, at what, and why.
    CHECK (status <> 'closed' OR (exit_date IS NOT NULL AND exit_price IS NOT NULL
                                  AND exit_reason IS NOT NULL AND net_pnl IS NOT NULL)),
    -- An open one must not.
    CHECK (status <> 'open' OR (exit_date IS NULL AND exit_price IS NULL
                                AND exit_reason IS NULL)),
    -- A pending resolution is NOT an exit: it may carry no exit price at all.
    CHECK (status <> 'pending_resolution' OR (exit_price IS NULL AND exit_reason IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_book
    ON paper_positions (book_id, status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_candidate
    ON paper_positions (candidate_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_exit
    ON paper_positions (exit_date, horizon_days);

-- Mirrors paper_positions and holds SPY. Same candidate_id and horizon_days, so
-- the pairing is by construction rather than by a join on dates that could
-- silently mismatch. It has no stop or target: it is a matched hold, not a
-- second application of the rule, and it closes on the same date as its pair.
CREATE TABLE IF NOT EXISTS benchmark_positions (
    position_id   TEXT PRIMARY KEY,
    candidate_id  TEXT NOT NULL REFERENCES research_candidates (candidate_id),
    horizon_days  INTEGER NOT NULL,
    book_id       TEXT NOT NULL REFERENCES books (book_id),
    -- The SPY security, resolved by security_id so the benchmark is not tied to
    -- a ticker string.
    security_id   INTEGER NOT NULL REFERENCES securities (security_id),

    protocol_version          TEXT NOT NULL,
    strategy_version          INTEGER NOT NULL,
    resolution_policy_version INTEGER NOT NULL,
    accrual_policy_version    INTEGER NOT NULL,
    price_snapshot_hash       TEXT,
    opened_run_id             TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    last_evaluated_at         TEXT NOT NULL,

    entry_date    TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    slippage_bps  REAL NOT NULL,
    shares        REAL NOT NULL,
    notional      REAL NOT NULL,

    status        TEXT NOT NULL CHECK (status IN ('open', 'closed', 'pending_resolution')),
    exit_date     TEXT,
    exit_price    REAL,
    exit_reason   TEXT CHECK (exit_reason IS NULL OR exit_reason = 'matched_close'),

    dividends_received REAL NOT NULL DEFAULT 0,
    splits_applied     REAL NOT NULL DEFAULT 1.0,

    gross_pnl     REAL,
    net_pnl       REAL,
    pnl_pct       REAL,

    requires_manual_review INTEGER NOT NULL DEFAULT 0
                                CHECK (requires_manual_review IN (0, 1)),

    UNIQUE (candidate_id, horizon_days),

    CHECK (shares > 0),
    CHECK (notional > 0),
    CHECK (entry_price > 0),
    CHECK (slippage_bps > 0),
    CHECK (splits_applied > 0),
    CHECK (status <> 'closed' OR (exit_date IS NOT NULL AND exit_price IS NOT NULL
                                  AND exit_reason IS NOT NULL AND net_pnl IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_benchmark_positions_candidate
    ON benchmark_positions (candidate_id, horizon_days);

-- A candidate whose entry never happened. Keyed by candidate alone, not by
-- horizon: the gap test runs once against one session open for one security, so
-- a cancellation applies to every book at once.
--
-- KNOWN LIMITATION: 'halted' is a declared reason with no data source behind
-- it. R1-PROTOCOL-1.1 step 4 reads "Halt, or no normal open: cancel and log" as
-- one combined check, and nothing in this dataset flags a trading halt
-- separately from a missing bar, so every case that would be 'halted' is
-- recorded as 'no_regular_open' instead -- true to the evidence available
-- rather than inferring a halt that was never observed. The code is in place
-- and the value stays declared so a halt feed can be wired in later without
-- another migration.
CREATE TABLE IF NOT EXISTS cancelled_entries (
    candidate_id    TEXT PRIMARY KEY REFERENCES research_candidates (candidate_id),
    reason          TEXT NOT NULL CHECK (reason IN (
                        'gap_above_prior_close',
                        'no_regular_open',
                        'halted',
                        'adv_below_protocol_bands')),
    signal_close    REAL,
    next_open       REAL,
    gap_atr         REAL,           -- (open - adjusted prior close) / ATR
    -- How the prior close was put on the same basis as the open: the raw close,
    -- the split ratio applied, and the result. This is what proves a
    -- cancellation was a real gap and not a split misread as one.
    adjusted_basis  TEXT NOT NULL,
    cancelled_at    TEXT NOT NULL,  -- UTC
    run_id          TEXT NOT NULL REFERENCES pipeline_runs (run_id)
);

CREATE INDEX IF NOT EXISTS idx_cancelled_entries_run
    ON cancelled_entries (run_id, reason);

-- ADDITION BEYOND THE SPECIFIED TABLES.
-- paper_positions carries dividends_received and splits_applied as running
-- scalars, but the protocol requires more than the totals: a dividend "accrues
-- on the ex-date, records its payment date, and enters P&L from the ex-date",
-- and the manual checklist asks for a split and a dividend to be traced end to
-- end. A scalar cannot hold a date or a sequence, so each corporate action
-- applied to a position is recorded here as it happens.
CREATE TABLE IF NOT EXISTS position_events (
    position_id   TEXT NOT NULL REFERENCES paper_positions (position_id),
    ex_date       TEXT NOT NULL,
    action_type   TEXT NOT NULL,
    -- Split ratio, or NULL for a cash event.
    ratio         REAL,
    -- Cash per share, or NULL for a split.
    cash_amount   REAL,
    -- Cash actually accrued to this position: shares * cash_amount, or 0 when
    -- the entitlement test failed.
    cash_accrued  REAL NOT NULL DEFAULT 0,
    entitled      INTEGER NOT NULL CHECK (entitled IN (0, 1)),
    -- The dividend payment date. NULL is the honest value here: the price
    -- vendor supplies ex-dates and amounts only, and no payment date is
    -- available for any action in this dataset. It is never inferred.
    payment_date  TEXT,
    shares_before REAL NOT NULL,
    shares_after  REAL NOT NULL,
    note          TEXT NOT NULL,

    PRIMARY KEY (position_id, ex_date, action_type)
);

CREATE INDEX IF NOT EXISTS idx_position_events_position
    ON position_events (position_id, ex_date);

PRAGMA foreign_keys = ON;
