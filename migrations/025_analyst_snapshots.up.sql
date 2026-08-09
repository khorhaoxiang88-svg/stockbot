-- 025 up: Analyst consensus snapshots (Wall Street price targets + rating
-- counts), sourced from Yahoo Finance via the same yfinance library
-- pipeline/prices/yfinance_provider.py already depends on -- see that file's
-- module docstring for the "personal use, not affiliated with Yahoo"
-- licensing note, which applies equally here.
--
-- This is third-party OPINION data, structurally unlike everything else this
-- system stores (SEC facts, prices, the bot's own composite score). It is
-- kept in its own table, never joined into scoring, selection or risk_flags,
-- and the web layer must label it as external analyst opinion, never as this
-- system's own assessment -- the same "candidate not recommendation"
-- boundary the language audit (web/tests/language-audit.test.ts) already
-- enforces for the bot's own copy applies here in spirit: real analysts'
-- "buy"/"sell" is quoted as data, never restated by the bot as advice.
--
-- Append-only, same discipline as news_events (024) and defect_log (023): an
-- analyst snapshot is a fact about what Yahoo reported at a point in time,
-- so a later fetch is a new row, never an overwrite of the old one. "Latest"
-- is just MAX(fetched_at) per security.

CREATE TABLE IF NOT EXISTS analyst_snapshots (
    security_id          INTEGER NOT NULL REFERENCES securities (security_id),
    fetched_at            TEXT NOT NULL,   -- UTC
    source                 TEXT NOT NULL DEFAULT 'yfinance',
    currency                TEXT,

    num_analysts             INTEGER,
    target_low                REAL,
    target_mean                 REAL,
    target_median                 REAL,
    target_high                     REAL,

    -- Yahoo's own consensus label ('buy', 'hold', etc) and its 1.0 (strong
    -- buy) - 5.0 (strong sell) mean. Stored verbatim; the web layer never
    -- echoes 'buy' literally (language audit), it relabels for display.
    recommendation_key                TEXT,
    recommendation_mean                  REAL CHECK (
                                            recommendation_mean IS NULL
                                            OR (recommendation_mean >= 1.0 AND recommendation_mean <= 5.0)
                                          ),

    -- Current-period (period '0m') rating bucket counts from Yahoo's
    -- recommendations_summary. Independent of recommendation_key/mean, which
    -- come from a separate Yahoo endpoint and can be absent when this is not.
    rating_strong_buy                     INTEGER CHECK (rating_strong_buy IS NULL OR rating_strong_buy >= 0),
    rating_buy                             INTEGER CHECK (rating_buy IS NULL OR rating_buy >= 0),
    rating_hold                             INTEGER CHECK (rating_hold IS NULL OR rating_hold >= 0),
    rating_sell                              INTEGER CHECK (rating_sell IS NULL OR rating_sell >= 0),
    rating_strong_sell                        INTEGER CHECK (rating_strong_sell IS NULL OR rating_strong_sell >= 0),

    fetch_error                                TEXT,   -- set, and every other column NULL, on a failed fetch

    PRIMARY KEY (security_id, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_analyst_snapshots_security
    ON analyst_snapshots (security_id, fetched_at DESC);

CREATE TRIGGER IF NOT EXISTS analyst_snapshots_no_update
BEFORE UPDATE ON analyst_snapshots
BEGIN
    SELECT RAISE(ABORT, 'analyst_snapshots is append-only: UPDATE is forbidden. Insert a new snapshot row.');
END;

CREATE TRIGGER IF NOT EXISTS analyst_snapshots_no_delete
BEFORE DELETE ON analyst_snapshots
BEGIN
    SELECT RAISE(ABORT, 'analyst_snapshots is append-only: DELETE is forbidden.');
END;
