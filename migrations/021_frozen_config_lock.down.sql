-- 021 down: drop frozen_config_lock.

DROP TRIGGER IF EXISTS frozen_config_lock_no_update;
DROP TRIGGER IF EXISTS frozen_config_lock_no_delete;
DROP TABLE IF EXISTS frozen_config_lock;
