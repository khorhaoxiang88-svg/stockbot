-- 024 up: News Ledger, Stage A (R2-NEWS-1.0), shadow mode.
--
-- Three new tables, all owned exclusively by the News pipeline. Nothing in
-- this migration touches securities, prices, scores, risk_flags, selection,
-- execution, experiments or frozen_config_lock -- it only reads securities.
-- security_id by foreign key, same as every other evidence table already
-- does (insider_transactions, dilution_signals, risk_flags).
--
-- ZERO SCORE INFLUENCE, Stage A: news_events carries no column any scoring
-- code could read as a bonus/penalty input, and nothing in pipeline/scoring,
-- pipeline/selection or pipeline/riskflags references these tables (see
-- pipeline/tests/test_news_ledger.py's source-scan test).
--
--   * news_filings: 8-K / 8-K-A filing index for News, deliberately separate
--     from the existing `filings` table (migration 004) rather than reused --
--     keeping News's write path structurally isolated from the table
--     pipeline/sec/ingest_facts.py already writes to makes "News never
--     touches an existing scoring-adjacent table" true by construction, not
--     by convention.
--   * news_filing_documents: every stored document (primary + exhibits)
--     under a news_filings accession. Payload bytes live in raw_payloads
--     (migration 004, source='sec', endpoint='8-K'), reused as-is -- it is
--     pure content-addressed storage with no scoring semantics attached.
--   * news_events: extracted structured events. APPEND-ONLY, enforced by
--     trigger, same discipline as xbrl_facts (migration 004). A correction
--     is a new row with supersedes_event_id set, never an UPDATE --
--     effective_news_events is the read-through view that hides a
--     superseded row without ever deleting or rewriting it.
--
-- Never infer an unstated amount (spec Sec.3): amount_explicit and
-- amount_stated are tied by CHECK so a row claiming "explicit" without a
-- value, or "unavailable" while carrying one, cannot be inserted.
--
-- Abstain is a stored state, never a defaulted tier (spec Sec.3): the CHECK
-- below requires an abstained row to carry NO event type and NO confirmation
-- tier, only a reason: and a non-abstained row to carry a confirmation tier
-- and NO reason. There is no third shape.

CREATE TABLE IF NOT EXISTS news_filings (
    accession_no      TEXT PRIMARY KEY,
    cik               TEXT NOT NULL,
    security_id       INTEGER REFERENCES securities (security_id),
    form_type         TEXT NOT NULL CHECK (form_type IN ('8-K', '8-K/A')),
    filed_date        TEXT,             -- date as reported by EDGAR
    accepted_at       TEXT,             -- UTC, NULL when unresolved
    period_of_report  TEXT,
    primary_doc_url   TEXT,
    payload_id        TEXT REFERENCES raw_payloads (payload_id),
    ingested_at       TEXT NOT NULL     -- UTC
);

CREATE INDEX IF NOT EXISTS idx_news_filings_security
    ON news_filings (security_id, filed_date DESC);
CREATE INDEX IF NOT EXISTS idx_news_filings_cik ON news_filings (cik, filed_date);

CREATE TABLE IF NOT EXISTS news_filing_documents (
    accession_no   TEXT NOT NULL REFERENCES news_filings (accession_no),
    document_name  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('primary', 'exhibit')),
    -- Exhibit number (e.g. 'EX-99.1') when it could be read off the filing
    -- index; NULL when it could not be -- never guessed from the filename.
    exhibit_label  TEXT,
    payload_id     TEXT NOT NULL REFERENCES raw_payloads (payload_id),

    PRIMARY KEY (accession_no, document_name)
);

CREATE TABLE IF NOT EXISTS news_events (
    event_id                    TEXT PRIMARY KEY,
    security_id                 INTEGER NOT NULL REFERENCES securities (security_id),
    accession_no                TEXT NOT NULL REFERENCES news_filings (accession_no),
    -- Denormalised from news_filings at insert time, same pattern xbrl_facts
    -- uses -- a fact/event is usable or not without a join.
    accepted_at                 TEXT,
    source_document              TEXT NOT NULL,   -- which document this came from
    extracted_at                  TEXT NOT NULL,   -- UTC, when extraction ran

    is_abstain                    INTEGER NOT NULL DEFAULT 0 CHECK (is_abstain IN (0, 1)),
    abstain_reason                 TEXT,

    event_type_candidate           TEXT CHECK (event_type_candidate IS NULL OR event_type_candidate IN (
                                       'binding_commercial_contract',
                                       'contract_termination',
                                       'lawsuit',
                                       'merger_acquisition',
                                       'financing_or_securities_issuance',
                                       'partnership_no_disclosed_commitment',
                                       'non_binding_loi_or_mou',
                                       'rumor_unnamed_source'
                                    )),
    confirmation_tier               TEXT CHECK (confirmation_tier IS NULL OR confirmation_tier IN (
                                       'binding', 'non_binding_loi', 'rumor'
                                    )),

    amount_explicit                  INTEGER NOT NULL CHECK (amount_explicit IN (0, 1)),
    amount_stated                     REAL,
    amount_type                        TEXT CHECK (amount_type IS NULL OR amount_type IN (
                                          'total', 'annual', 'minimum', 'maximum', 'estimated'
                                       )),
    currency                            TEXT,
    contract_duration_months             INTEGER,
    annualization_method                  TEXT,
    includes_optional_extensions           INTEGER CHECK (
                                              includes_optional_extensions IS NULL
                                              OR includes_optional_extensions IN (0, 1)
                                           ),

    -- The literal text the classification was drawn from -- never optional.
    supporting_passage                      TEXT NOT NULL,
    passage_source_offset                    INTEGER,

    extraction_model_version                  TEXT NOT NULL,
    extraction_prompt_version                  TEXT NOT NULL,

    -- Correction path: a new row points back at what it supersedes. Never an
    -- UPDATE on the old row -- see the append-only triggers below.
    supersedes_event_id                        TEXT REFERENCES news_events (event_id),

    CHECK (
        (is_abstain = 1 AND event_type_candidate IS NULL AND confirmation_tier IS NULL
             AND abstain_reason IS NOT NULL)
        OR
        (is_abstain = 0 AND abstain_reason IS NULL AND confirmation_tier IS NOT NULL)
    ),
    CHECK (
        (amount_explicit = 1 AND amount_stated IS NOT NULL)
        OR (amount_explicit = 0 AND amount_stated IS NULL)
    ),
    CHECK (supersedes_event_id IS NULL OR supersedes_event_id <> event_id)
);

CREATE INDEX IF NOT EXISTS idx_news_events_security
    ON news_events (security_id, extracted_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_accession ON news_events (accession_no);
CREATE INDEX IF NOT EXISTS idx_news_events_supersedes ON news_events (supersedes_event_id);

CREATE TRIGGER IF NOT EXISTS news_events_no_update
BEFORE UPDATE ON news_events
BEGIN
    SELECT RAISE(ABORT, 'news_events is append-only: UPDATE is forbidden. Insert a new event row with supersedes_event_id set.');
END;

CREATE TRIGGER IF NOT EXISTS news_events_no_delete
BEFORE DELETE ON news_events
BEGIN
    SELECT RAISE(ABORT, 'news_events is append-only: DELETE is forbidden.');
END;

-- The currently-effective set: every event that nothing else supersedes.
CREATE VIEW IF NOT EXISTS effective_news_events AS
SELECT e.*
  FROM news_events e
 WHERE NOT EXISTS (
     SELECT 1 FROM news_events e2 WHERE e2.supersedes_event_id = e.event_id
 );
