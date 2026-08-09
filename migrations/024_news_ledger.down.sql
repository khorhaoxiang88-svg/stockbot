-- 024 down: drop the News Ledger tables. raw_payloads (migration 004) and
-- securities are untouched -- this migration never owned them.

DROP VIEW IF EXISTS effective_news_events;
DROP TRIGGER IF EXISTS news_events_no_delete;
DROP TRIGGER IF EXISTS news_events_no_update;
DROP INDEX IF EXISTS idx_news_events_supersedes;
DROP INDEX IF EXISTS idx_news_events_accession;
DROP INDEX IF EXISTS idx_news_events_security;
DROP TABLE IF EXISTS news_events;

DROP TABLE IF EXISTS news_filing_documents;

DROP INDEX IF EXISTS idx_news_filings_cik;
DROP INDEX IF EXISTS idx_news_filings_security;
DROP TABLE IF EXISTS news_filings;
