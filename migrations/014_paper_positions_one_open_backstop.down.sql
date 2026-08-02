-- 014 down: drop the DB-level one-open-position-per-security-horizon backstop.
--
-- Triggers only; nothing else in this migration touches data or schema, so
-- dropping them is a straightforward, lossless rollback.

DROP TRIGGER IF EXISTS paper_positions_one_open_per_security_horizon_update;
DROP TRIGGER IF EXISTS paper_positions_one_open_per_security_horizon_insert;
