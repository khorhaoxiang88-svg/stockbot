-- 004 down: drop everything 004 created.
--
-- The append-only triggers block UPDATE and DELETE, not DROP, so the table can
-- still be removed here. Dropping the table drops its triggers with it.

DROP VIEW IF EXISTS usable_facts;

DROP TRIGGER IF EXISTS xbrl_facts_no_delete;
DROP TRIGGER IF EXISTS xbrl_facts_no_update;

DROP INDEX IF EXISTS idx_xbrl_facts_payload;
DROP INDEX IF EXISTS idx_xbrl_facts_accession;
DROP INDEX IF EXISTS idx_xbrl_facts_context;
DROP INDEX IF EXISTS idx_xbrl_facts_semantic;
DROP INDEX IF EXISTS idx_xbrl_facts_lookup;
DROP TABLE IF EXISTS xbrl_facts;

DROP INDEX IF EXISTS idx_filings_cik;
DROP TABLE IF EXISTS filings;

DROP INDEX IF EXISTS idx_raw_payloads_identifier;
DROP INDEX IF EXISTS idx_raw_payloads_hash;
DROP TABLE IF EXISTS raw_payloads;
