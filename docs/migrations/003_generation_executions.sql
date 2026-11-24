-- Apply after 002_generation_attempts.sql. Historical Work records are untouched.
CREATE TABLE IF NOT EXISTS `generation_executions` (
  `work_id` BIGINT UNSIGNED NOT NULL,
  `lease_owner` CHAR(32) NULL,
  `lease_generation` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `lease_expires_at` DATETIME(6) NULL,
  `last_heartbeat_at` DATETIME(6) NULL,
  `recover_after` DATETIME(6) NOT NULL,
  `started_at` DATETIME(6) NOT NULL,
  `legacy_uncertain` TINYINT NOT NULL DEFAULT 0,
  `artifact_key` VARCHAR(255) NULL,
  `artifact_size` BIGINT UNSIGNED NULL,
  `artifact_sha256` CHAR(64) NULL,
  PRIMARY KEY (`work_id`),
  KEY `idx_execution_recover` (`recover_after`, `lease_expires_at`, `work_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
