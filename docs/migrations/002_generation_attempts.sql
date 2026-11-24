-- Compatible expansion. Apply to the selected application database.
CREATE TABLE IF NOT EXISTS `generation_attempts` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `work_id` BIGINT UNSIGNED NOT NULL,
  `attempt_no` INT NOT NULL,
  `request_id` CHAR(32) NOT NULL,
  `provider` VARCHAR(32) NOT NULL,
  `provider_task_id` VARCHAR(128) NULL,
  `status` VARCHAR(24) NOT NULL,
  `retry_create` TINYINT NOT NULL DEFAULT 0,
  `request_started_at` DATETIME(6) NULL,
  `response_received_at` DATETIME(6) NULL,
  `last_checked_at` DATETIME(6) NULL,
  `next_check_at` DATETIME(6) NULL,
  `error_code` VARCHAR(64) NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_attempt_work_no` (`work_id`, `attempt_no`),
  UNIQUE KEY `uk_attempt_request` (`request_id`),
  UNIQUE KEY `uk_attempt_provider_task` (`provider`, `provider_task_id`),
  KEY `idx_attempt_due` (`status`, `next_check_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
