-- 003 up: raw daily prices, revision audit trail, dataset versioning,
-- provider provenance, and the corporate actions ledger.
--
-- Two deliberate rules encoded here:
--
--   1. RAW PRICES ONLY. There is no adjusted_close column. Adjustment is
--      computed at read time from corporate_actions, so a later correction to a
--      split ratio does not require rewriting price history.
--
--   2. Dates vs timestamps. `date` and `ex_date` are US market TRADING DATES,
--      that is Eastern calendar dates, because a daily bar belongs to a trading
--      session and not to a UTC instant. Every actual timestamp column
--      (first_seen_at, last_verified_at, detected_at, accepted_at, created_at,
--      valid_from, valid_to) is UTC, matching the rest of the system.

-- One row per global version of the price dataset. A new version is created
-- only when data actually changes, so re-running ingestion on unchanged data
-- adds nothing here.
CREATE TABLE IF NOT EXISTS price_dataset_versions (
    dataset_version   INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL,          -- UTC
    provider          TEXT NOT NULL,
    reason            TEXT NOT NULL,
    changed_row_count INTEGER NOT NULL DEFAULT 0,
    run_id            TEXT REFERENCES pipeline_runs (run_id)
);

-- Canonical raw traded OHLCV. One row per security per trading date.
CREATE TABLE IF NOT EXISTS prices (
    security_id        INTEGER NOT NULL REFERENCES securities (security_id),
    date               TEXT NOT NULL,          -- ET trading date, YYYY-MM-DD
    open               REAL,
    high               REAL,
    low                REAL,
    close              REAL,
    volume             INTEGER,
    provider           TEXT NOT NULL,
    first_seen_at      TEXT NOT NULL,          -- UTC
    last_verified_at   TEXT NOT NULL,          -- UTC
    revision           INTEGER NOT NULL DEFAULT 0,
    price_data_version INTEGER NOT NULL REFERENCES price_dataset_versions (dataset_version),
    PRIMARY KEY (security_id, date),
    CHECK (volume IS NULL OR volume >= 0),
    CHECK (revision >= 0)
);

CREATE INDEX IF NOT EXISTS idx_prices_date ON prices (date);
CREATE INDEX IF NOT EXISTS idx_prices_version ON prices (price_data_version);

-- Every vendor correction, with the complete before and after picture. A
-- correction is never absorbed silently: it lands here first.
CREATE TABLE IF NOT EXISTS price_revisions (
    security_id                INTEGER NOT NULL REFERENCES securities (security_id),
    date                       TEXT NOT NULL,   -- ET trading date
    revision                   INTEGER NOT NULL,
    old_open                   REAL,
    old_high                   REAL,
    old_low                    REAL,
    old_close                  REAL,
    old_volume                 INTEGER,
    new_open                   REAL,
    new_high                   REAL,
    new_low                    REAL,
    new_close                  REAL,
    new_volume                 INTEGER,
    detected_at                TEXT NOT NULL,   -- UTC
    accepted_at                TEXT,            -- UTC, NULL until accepted
    provider                   TEXT NOT NULL,
    price_data_version_before  INTEGER REFERENCES price_dataset_versions (dataset_version),
    price_data_version_after   INTEGER REFERENCES price_dataset_versions (dataset_version),
    PRIMARY KEY (security_id, date, revision),
    CHECK (revision >= 1)
);

CREATE INDEX IF NOT EXISTS idx_price_revisions_security
    ON price_revisions (security_id, date);
CREATE INDEX IF NOT EXISTS idx_price_revisions_version
    ON price_revisions (price_data_version_after);

-- Which provider supplied a security's series over which period. Splicing two
-- providers into one continuous series is forbidden: a switch means refetching
-- the whole history and closing the previous window here.
CREATE TABLE IF NOT EXISTS price_series_provenance (
    security_id   INTEGER NOT NULL REFERENCES securities (security_id),
    provider      TEXT NOT NULL,
    valid_from    TEXT NOT NULL,   -- UTC
    valid_to      TEXT,            -- UTC, NULL means current
    switch_reason TEXT,
    PRIMARY KEY (security_id, provider, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

-- Corporate actions ledger. Adjustment factors are derived from this at read
-- time, never baked into the price rows.
CREATE TABLE IF NOT EXISTS corporate_actions (
    security_id            INTEGER NOT NULL REFERENCES securities (security_id),
    ex_date                TEXT NOT NULL,   -- ET trading date
    action_type            TEXT NOT NULL
                                CHECK (action_type IN ('split', 'dividend', 'spinoff',
                                                       'merger', 'rights', 'other')),
    ratio                  REAL,            -- split: new shares per old share (10.0 = 10-for-1)
    cash_amount            REAL,            -- dividend: cash per share
    provider               TEXT NOT NULL,
    requires_manual_review INTEGER NOT NULL DEFAULT 0
                                CHECK (requires_manual_review IN (0, 1)),
    PRIMARY KEY (security_id, ex_date, action_type),
    -- A split without a ratio, or a dividend without an amount, is unusable.
    CHECK (action_type <> 'split' OR (ratio IS NOT NULL AND ratio > 0)),
    CHECK (action_type <> 'dividend' OR cash_amount IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_security
    ON corporate_actions (security_id, ex_date);
