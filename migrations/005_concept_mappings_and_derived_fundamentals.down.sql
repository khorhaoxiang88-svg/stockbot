-- 005 down: drop everything 005 created.

DROP VIEW IF EXISTS latest_fundamentals;

DROP INDEX IF EXISTS idx_derived_fundamentals_applicable;
DROP INDEX IF EXISTS idx_derived_fundamentals_security;
DROP TABLE IF EXISTS derived_fundamentals;

DROP INDEX IF EXISTS idx_concept_mappings_lookup;
DROP TABLE IF EXISTS concept_mappings;
