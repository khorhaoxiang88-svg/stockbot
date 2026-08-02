-- 011 up: research candidates, the suppression log, positions and the two books.
--
-- NUMBERING NOTE: the F10 brief called this "migration 010", but 010
-- (risk_flags) was already applied in F9. Editing an applied migration is
-- forbidden by the project's standing rules, so this is 011. The specified
-- column lists are unchanged.
--
-- ADDITION BEYOND THE SPECIFIED TABLES: `positions`. The brief specifies three
-- tables but states four rules that cannot be expressed without a fourth:
--   * "At most one open position per (security_id, horizon_days)"
--   * "A qualifying signal for a security already holding that horizon is
--      LOGGED to suppressed_signals with reason open_position"
--   * "Any position exited within the trailing 10 trading days: ineligible"
--   * "Gap-cancelled within the trailing 3 trading days: ineligible"
--   * books.open_position_count, which has to count something
-- Without a positions table those rules are unimplementable and untestable, so
-- the table is added here rather than the rules quietly dropped. It carries the
-- minimum the rules need and nothing speculative.
--
-- Four ideas carry this migration.
--
-- 1. APPEND-ONLY, ENFORCED BY TRIGGERS. A research candidate is a record of a
--    decision taken at a moment with the evidence available then. Updating one
--    destroys the only thing it is for. UPDATE and DELETE both raise, the same
--    way xbrl_facts is protected in migration 004.
--
-- 2. row_hash IS THE OFFICIALITY TEST. The brief says a manual edit voids the
--    run and marks affected records non-official, but specifies no "official"
--    column. It does not need one: row_hash covers every other field, so a
--    record whose recomputed hash does not match its stored hash has been
--    tampered with and is non-official by definition. That is a stronger
--    guarantee than a boolean anyone could also edit.
--
-- 3. A SUPPRESSION IS EVIDENCE. Everything that qualified and was not selected
--    is logged with a reason. A candidate list with no suppression log cannot
--    be audited: there is no way to tell a security that failed a rule from one
--    the code never considered.
--
-- 4. THE TWO BOOKS ARE NOT TWO SAMPLES. One selection produces up to five
--    candidates, and each candidate opens a position in BOTH books. Five
--    candidates therefore become ten positions but remain five independent
--    observations. research_candidates deliberately has no horizon column, so
--    the unique originating candidate count is impossible to lose.

CREATE TABLE IF NOT EXISTS research_candidates (
    candidate_id        TEXT PRIMARY KEY,
    security_id         INTEGER NOT NULL REFERENCES securities (security_id),

    ------------------------------------------------------------- when and from what
    generated_at        TEXT NOT NULL,      -- UTC, when the run produced this row
    -- The evidence horizon. Nothing accepted after this instant may influence
    -- the candidate. This is the week's closing cutoff, not the run time.
    data_cutoff_at      TEXT NOT NULL,      -- UTC
    snapshot_id         TEXT NOT NULL REFERENCES universe_snapshot_runs (snapshot_id),
    pipeline_run_id     TEXT NOT NULL REFERENCES pipeline_runs (run_id),

    --------------------------------------------------------------------- provenance
    strategy_version        INTEGER NOT NULL,
    config_hash             TEXT NOT NULL,
    code_version            TEXT NOT NULL,
    selection_rule_version  INTEGER NOT NULL,
    mapping_version         TEXT NOT NULL,
    price_dataset_version   INTEGER,
    price_snapshot_hash     TEXT,
    -- What every source looked like at generation, so a later argument about
    -- freshness is settled by the record rather than by memory.
    source_health_snapshot_json TEXT NOT NULL,
    -- The F8 score row and its explanation as they stood at the cutoff.
    score_snapshot_json     TEXT NOT NULL,
    -- Every accession that influenced this candidate. Each one is asserted to
    -- have accepted_at <= data_cutoff_at before the row is written.
    accessions_used_json    TEXT NOT NULL,

    ------------------------------------------------------------------ the decision
    composite_at_generation REAL NOT NULL,
    rank_at_generation      INTEGER NOT NULL,

    ------------------------------------------------------- execution inputs, not rules
    -- The signal close is the last regular-session close at the cutoff. The gap
    -- filter is applied at the NEXT session's open and is an execution rule, so
    -- a candidate cancelled by it still lives here and still counts toward the
    -- reported cancellation rate.
    signal_close        REAL NOT NULL,
    atr_value           REAL,
    atr_window          INTEGER NOT NULL,
    price_data_cutoff   TEXT NOT NULL,      -- ET trading date of the signal close
    entry_rule          TEXT NOT NULL,
    gap_limit_atr       REAL NOT NULL,

    row_hash            TEXT NOT NULL,

    CHECK (composite_at_generation BETWEEN 0 AND 100),
    CHECK (rank_at_generation >= 1),
    CHECK (signal_close > 0),
    CHECK (atr_window > 0),
    CHECK (gap_limit_atr > 0)
);

CREATE INDEX IF NOT EXISTS idx_candidates_security
    ON research_candidates (security_id, data_cutoff_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_cutoff
    ON research_candidates (data_cutoff_at DESC, rank_at_generation);
CREATE INDEX IF NOT EXISTS idx_candidates_run
    ON research_candidates (pipeline_run_id);

-- Append-only. Same protection as xbrl_facts: a decision record that can be
-- rewritten is not a record.
CREATE TRIGGER IF NOT EXISTS research_candidates_no_update
BEFORE UPDATE ON research_candidates
BEGIN
    SELECT RAISE(ABORT, 'research_candidates is append-only: a candidate records a decision made with the evidence available at data_cutoff_at and is never updated');
END;

CREATE TRIGGER IF NOT EXISTS research_candidates_no_delete
BEFORE DELETE ON research_candidates
BEGIN
    SELECT RAISE(ABORT, 'research_candidates is append-only: deleting a candidate destroys the record of what was decided and when');
END;

-- Everything that was considered and not selected, with the reason.
CREATE TABLE IF NOT EXISTS suppressed_signals (
    run_id             TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    security_id        INTEGER NOT NULL REFERENCES securities (security_id),
    -- Which book the suppression applies to. Selection-level reasons (caps,
    -- threshold, eligibility) are logged once per horizon so each book's log
    -- independently answers "what qualified and was not selected".
    horizon_days       INTEGER NOT NULL,
    composite          REAL,
    "rank"             INTEGER,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                            -- eligibility
                            'not_rankable',
                            'model_not_applicable',
                            'dilution_disqualified',
                            'risk_flag_going_concern',
                            'risk_flag_dilution_disqualify',
                            'below_composite_threshold',
                            'composite_threshold_unset',
                            'stale_source',
                            -- cooldowns
                            'cooldown_recent_exit',
                            'cooldown_gap_cancelled',
                            -- capacity and caps
                            'open_position',
                            'book_capacity',
                            'cohort_cap',
                            'selection_cap')),
    -- Free text carrying the specific numbers behind the reason.
    detail             TEXT,

    PRIMARY KEY (run_id, security_id, horizon_days, suppression_reason)
);

CREATE INDEX IF NOT EXISTS idx_suppressed_run
    ON suppressed_signals (run_id, suppression_reason);
CREATE INDEX IF NOT EXISTS idx_suppressed_security
    ON suppressed_signals (security_id, run_id);

-- One book per horizon. An experimental accounting convention for measuring the
-- rule, NOT recommended position sizing: every position is the same $1,000
-- notional regardless of conviction, price or volatility, and nothing compounds.
CREATE TABLE IF NOT EXISTS books (
    book_id             TEXT PRIMARY KEY,
    horizon_days        INTEGER NOT NULL UNIQUE,
    starting_nav        REAL NOT NULL,
    current_nav         REAL NOT NULL,
    open_position_count INTEGER NOT NULL DEFAULT 0,
    strategy_version    INTEGER NOT NULL,

    CHECK (horizon_days > 0),
    CHECK (starting_nav > 0),
    CHECK (current_nav >= 0),
    CHECK (open_position_count >= 0)
);

-- See the ADDITION note at the top of this file.
CREATE TABLE IF NOT EXISTS positions (
    position_id   TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES books (book_id),
    candidate_id  TEXT NOT NULL REFERENCES research_candidates (candidate_id),
    security_id   INTEGER NOT NULL REFERENCES securities (security_id),
    horizon_days  INTEGER NOT NULL,

    -- 'pending' exists between selection at the week's close and the execution
    -- decision at the next open. The gap filter resolves it to 'open' or
    -- 'gap_cancelled'; a cancelled position keeps its candidate.
    status        TEXT NOT NULL CHECK (status IN
                       ('pending', 'open', 'closed', 'gap_cancelled')),
    notional      REAL NOT NULL,

    opened_on     TEXT,                     -- ET trading date, NULL until filled
    closed_on     TEXT,                     -- ET trading date
    exit_reason   TEXT,

    CHECK (status <> 'open'          OR (opened_on IS NOT NULL AND closed_on IS NULL)),
    CHECK (status <> 'closed'        OR (opened_on IS NOT NULL AND closed_on IS NOT NULL
                                         AND exit_reason IS NOT NULL)),
    CHECK (status <> 'gap_cancelled' OR (opened_on IS NULL AND closed_on IS NOT NULL)),
    CHECK (notional > 0)
);

-- "At most one open position per (security_id, horizon_days)", enforced rather
-- than checked in code. A partial index applies the rule only to open rows, so
-- the same security may be held again after a previous position closes.
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_one_open_per_security_horizon
    ON positions (security_id, horizon_days) WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_positions_book ON positions (book_id, status);
CREATE INDEX IF NOT EXISTS idx_positions_cooldown
    ON positions (security_id, closed_on DESC, status);

-- The most recent selection, for the /candidates page. Point-in-time queries
-- must NOT use this; they filter research_candidates on data_cutoff_at.
CREATE VIEW IF NOT EXISTS latest_selection AS
SELECT c.*
  FROM research_candidates c
 WHERE c.data_cutoff_at = (SELECT MAX(data_cutoff_at) FROM research_candidates);
