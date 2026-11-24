-- Run against the selected application database before starting the updated API.
CREATE TABLE IF NOT EXISTS `generation_outbox` (
  `work_id` BIGINT UNSIGNED NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `dispatch_after` DATETIME(6) NOT NULL,
  `lease_token` CHAR(32) NULL,
  `leased_until` DATETIME(6) NULL,
  `attempts` INT NOT NULL DEFAULT 0,
  `published_at` DATETIME(6) NULL,
  `received_at` DATETIME(6) NULL,
  `last_error` VARCHAR(64) NULL,
  PRIMARY KEY (`work_id`),
  KEY `idx_outbox_due` (`received_at`, `dispatch_after`, `work_id`),
  KEY `idx_outbox_created` (`received_at`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
