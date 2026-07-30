-- 006 down: drop everything 006 created.

DROP VIEW IF EXISTS effective_insider_transactions;
DROP VIEW IF EXISTS scored_insider_purchases;

DROP INDEX IF EXISTS idx_insider_owner;
DROP INDEX IF EXISTS idx_insider_superseded;
DROP INDEX IF EXISTS idx_insider_code;
DROP INDEX IF EXISTS idx_insider_security;

DROP TABLE IF EXISTS insider_transactions;
