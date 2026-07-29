-- 003 down: drop everything 003 created, children before parents.

DROP INDEX IF EXISTS idx_corporate_actions_security;
DROP TABLE IF EXISTS corporate_actions;

DROP TABLE IF EXISTS price_series_provenance;

DROP INDEX IF EXISTS idx_price_revisions_version;
DROP INDEX IF EXISTS idx_price_revisions_security;
DROP TABLE IF EXISTS price_revisions;

DROP INDEX IF EXISTS idx_prices_version;
DROP INDEX IF EXISTS idx_prices_date;
DROP TABLE IF EXISTS prices;

DROP TABLE IF EXISTS price_dataset_versions;
