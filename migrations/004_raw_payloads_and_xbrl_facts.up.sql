-- 004 up: raw payload preservation and normalised XBRL accounting facts.
--
-- The whole point of this migration is that it CANNOT be retrofitted. Once
-- source facts have been collapsed or overwritten, the information needed to
-- validate them later is gone for good. So:
--
--   * xbrl_facts is APPEND-ONLY, enforced by triggers below, not by convention.
--   * Uniqueness is on (payload_id, source_fact_key), which is SOURCE identity.
--     Two source facts that happen to normalise to the same semantic fields are
--     both kept. A restatement is exactly that case, and losing one half of it
--     would destroy the audit trail.
--   * semantic_hash and context_hash are computed for grouping and duplicate
--     DETECTION only. Neither is ever a uniqueness constraint.
--
-- All timestamps are UTC. filed_date and period_of_report are dates as reported.

-- Metadata for every raw response kept on disk. SQLite stores no payload bytes;
-- the file lives at data/raw/{source}/{yyyy}/{mm}/{content_hash}.json.gz and its
-- hash is verified before any reprocessing.
CREATE TABLE IF NOT EXISTS raw_payloads (
    payload_id    TEXT PRIMARY KEY,
    source        TEXT NOT NULL,          -- 'sec'
    endpoint      TEXT NOT NULL,          -- 'companyfacts' | 'submissions'
    identifier    TEXT NOT NULL,          -- 'CIK0000320193'
    relative_path TEXT NOT NULL,          -- path under the repo root
    content_hash  TEXT NOT NULL,          -- sha256 of the UNCOMPRESSED bytes
    byte_size     INTEGER NOT NULL,       -- uncompressed size
    fetched_at    TEXT NOT NULL,          -- UTC
    -- Re-fetching identical content reuses the existing payload instead of
    -- creating a second row, which is what keeps fact ingestion idempotent.
    UNIQUE (source, endpoint, identifier, content_hash),
    CHECK (byte_size >= 0)
);

CREATE INDEX IF NOT EXISTS idx_raw_payloads_hash ON raw_payloads (content_hash);
CREATE INDEX IF NOT EXISTS idx_raw_payloads_identifier
    ON raw_payloads (source, endpoint, identifier);

-- Filing metadata, the source of acceptance timestamps. filed_date alone is not
-- enough for an intraday cutoff, so accepted_at is resolved from EDGAR
-- submissions metadata via the accession number.
CREATE TABLE IF NOT EXISTS filings (
    accession_no     TEXT PRIMARY KEY,
    cik              TEXT NOT NULL,
    form_type        TEXT,
    filed_date       TEXT,                -- date as reported by EDGAR
    accepted_at      TEXT,                -- UTC timestamp, NULL when unresolved
    period_of_report TEXT,
    primary_doc_url  TEXT,
    payload_id       TEXT REFERENCES raw_payloads (payload_id)
);

CREATE INDEX IF NOT EXISTS idx_filings_cik ON filings (cik, filed_date);

-- Normalised accounting facts. One row per fact per source payload.
CREATE TABLE IF NOT EXISTS xbrl_facts (
    fact_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_id               TEXT NOT NULL REFERENCES raw_payloads (payload_id),
    -- Stable identifier of this fact WITHIN its source document. For Company
    -- Facts this is taxonomy|concept|unit|position, the position being the
    -- fact's index in the source array.
    source_fact_key          TEXT NOT NULL,
    cik                      TEXT NOT NULL,
    taxonomy                 TEXT NOT NULL,
    concept                  TEXT NOT NULL,
    unit                     TEXT,
    context_type             TEXT CHECK (context_type IN ('instant', 'duration')),
    period_start             TEXT,
    period_end               TEXT,
    -- NULL from companyfacts: that endpoint returns consolidated facts only and
    -- carries no dimensional members. Not fabricated.
    dimensions_json          TEXT,
    context_hash             TEXT NOT NULL,
    semantic_hash            TEXT NOT NULL,
    frame                    TEXT,
    raw_value                TEXT,
    normalized_numeric_value REAL,
    -- NULL from companyfacts: the endpoint does not return the XBRL decimals
    -- attribute or nil flags. Release 1 does not parse instance documents.
    decimals                 INTEGER,
    is_nil                   INTEGER CHECK (is_nil IN (0, 1)),
    fiscal_year              INTEGER,
    fiscal_period            TEXT,
    form_type                TEXT,
    accession_no             TEXT,
    filed_date               TEXT,
    -- NULL means acceptance time could not be resolved. Such a fact is unusable
    -- for official candidates; see the usable_facts view.
    accepted_at              TEXT,
    source_endpoint          TEXT NOT NULL,

    -- SOURCE identity. Never semantic identity.
    UNIQUE (payload_id, source_fact_key)
);

CREATE INDEX IF NOT EXISTS idx_xbrl_facts_lookup
    ON xbrl_facts (cik, concept, period_end);
CREATE INDEX IF NOT EXISTS idx_xbrl_facts_semantic ON xbrl_facts (semantic_hash);
CREATE INDEX IF NOT EXISTS idx_xbrl_facts_context ON xbrl_facts (context_hash);
CREATE INDEX IF NOT EXISTS idx_xbrl_facts_accession ON xbrl_facts (accession_no);
CREATE INDEX IF NOT EXISTS idx_xbrl_facts_payload ON xbrl_facts (payload_id);

-- Append-only, enforced. A future bug that tries to "fix" a fact in place fails
-- loudly instead of quietly destroying the record of what the source said.
CREATE TRIGGER IF NOT EXISTS xbrl_facts_no_update
BEFORE UPDATE ON xbrl_facts
BEGIN
    SELECT RAISE(ABORT, 'xbrl_facts is append-only: UPDATE is forbidden. Insert a new fact row.');
END;

CREATE TRIGGER IF NOT EXISTS xbrl_facts_no_delete
BEFORE DELETE ON xbrl_facts
BEGIN
    SELECT RAISE(ABORT, 'xbrl_facts is append-only: DELETE is forbidden.');
END;

-- A fact without a resolved acceptance timestamp cannot be used for official
-- candidates, because an intraday cutoff cannot be applied to it.
CREATE VIEW IF NOT EXISTS usable_facts AS
SELECT * FROM xbrl_facts WHERE accepted_at IS NOT NULL;
