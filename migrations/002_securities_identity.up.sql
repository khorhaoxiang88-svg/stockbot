-- 002 up: internal identity model, listings history, universe snapshots, fixture manifest.
--
-- Core rule: a ticker symbol is NEVER a primary key. Symbols change and get
-- reused by unrelated companies. security_id is the only stable identity.
--
-- security_id is INTEGER PRIMARY KEY AUTOINCREMENT. In SQLite, AUTOINCREMENT
-- guarantees a value is never reused even after rows are deleted. Rows in this
-- table must still never be deleted: a security that stops trading is marked
-- is_active = 0 with a delisted_date.
--
-- All timestamp columns store UTC. Date-only columns store UTC calendar dates.

CREATE TABLE IF NOT EXISTS securities (
    security_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cik                       TEXT,           -- 10-digit zero-padded, NULL if no SEC registrant
    share_class               TEXT,           -- 'A', 'B', 'COMMON', NULL if not applicable
    name                      TEXT NOT NULL,
    security_type             TEXT NOT NULL DEFAULT 'unknown'
                                   CHECK (security_type IN (
                                       'common_stock', 'preferred_share', 'warrant', 'right',
                                       'unit', 'etf', 'etn', 'closed_end_fund', 'adr',
                                       'trust_unit', 'test_issue', 'unknown')),
    classification_confidence TEXT NOT NULL DEFAULT 'low'
                                   CHECK (classification_confidence IN ('high', 'medium', 'low')),
    classification_source     TEXT NOT NULL,  -- which rule/source decided the type
    sic_code                  TEXT,           -- SEC industry code; 6022 bank, 6798 REIT
    first_seen                TEXT NOT NULL,  -- UTC
    last_seen                 TEXT NOT NULL,  -- UTC
    is_active                 INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    delisted_date             TEXT,           -- UTC date, NULL while listed

    -- An unresolved security may never carry high confidence. Enforced here so
    -- no code path can bypass it.
    CHECK (NOT (security_type = 'unknown' AND classification_confidence = 'high')),
    -- A delisted security must record when, and an active one must not.
    CHECK ((is_active = 1 AND delisted_date IS NULL)
        OR (is_active = 0 AND delisted_date IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_securities_cik ON securities (cik);
CREATE INDEX IF NOT EXISTS idx_securities_type ON securities (security_type);

-- Symbol history. One row per (security, symbol, start date). A symbol reused
-- later by a different company produces a row under a different security_id.
CREATE TABLE IF NOT EXISTS listings (
    security_id INTEGER NOT NULL REFERENCES securities (security_id),
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    valid_from  TEXT NOT NULL,   -- UTC date, inclusive
    valid_to    TEXT,            -- UTC date, exclusive. NULL means still current.
    is_primary  INTEGER NOT NULL DEFAULT 1 CHECK (is_primary IN (0, 1)),
    PRIMARY KEY (security_id, symbol, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX IF NOT EXISTS idx_listings_symbol_window
    ON listings (symbol, valid_from, valid_to);

-- One row per universe build. is_official marks the snapshot the book trades from.
CREATE TABLE IF NOT EXISTS universe_snapshot_runs (
    snapshot_id    TEXT PRIMARY KEY,
    effective_at   TEXT NOT NULL,             -- UTC
    rules_version  TEXT NOT NULL,
    config_hash    TEXT NOT NULL,
    run_id         TEXT REFERENCES pipeline_runs (run_id),
    security_count INTEGER NOT NULL DEFAULT 0,
    is_official    INTEGER NOT NULL DEFAULT 0 CHECK (is_official IN (0, 1))
);

CREATE TABLE IF NOT EXISTS universe_snapshots (
    snapshot_id           TEXT NOT NULL REFERENCES universe_snapshot_runs (snapshot_id),
    security_id           INTEGER NOT NULL REFERENCES securities (security_id),
    snapshot_date         TEXT NOT NULL,      -- UTC date
    status                TEXT NOT NULL CHECK (status IN ('included', 'excluded', 'watch')),
    exclusion_reason      TEXT,
    adv_dollar            REAL,
    market_cap            REAL,
    market_cap_confidence TEXT CHECK (market_cap_confidence IN ('high', 'medium', 'low')),
    days_below_retention  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, security_id),
    -- An exclusion must say why.
    CHECK (status <> 'excluded' OR exclusion_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_universe_snapshots_security
    ON universe_snapshots (security_id, snapshot_date);

-- The 50-security engineering fixture. Version-controlled in
-- pipeline/universe/fixture_manifest.json and loaded into this table.
CREATE TABLE IF NOT EXISTS fixture_manifest (
    security_id         INTEGER NOT NULL REFERENCES securities (security_id),
    symbol_at_selection TEXT NOT NULL,
    inclusion_reason    TEXT NOT NULL,
    category            TEXT NOT NULL,
    added_at            TEXT NOT NULL,        -- UTC
    manifest_version    TEXT NOT NULL,
    PRIMARY KEY (security_id, manifest_version)
);

CREATE INDEX IF NOT EXISTS idx_fixture_manifest_version
    ON fixture_manifest (manifest_version, category);
