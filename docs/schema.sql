-- 图生视频创作平台 · MySQL 8.0 建表脚本（与 docs/requirements.md v5.2 对齐）
-- 修订：works.seq 改为可空（草稿阶段尚未分配序号）
-- 文件编码：UTF-8 无 BOM；换行：LF；容器首次启动时由 /docker-entrypoint-initdb.d 自动执行

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS `i2v_platform` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `i2v_platform`;

-- 1. 考生表 (candidates)
CREATE TABLE `candidates` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account` VARCHAR(64) NOT NULL COMMENT '学号/考号，全局唯一',
  `name` VARCHAR(64) NOT NULL COMMENT '考生姓名',
  `password_hash` VARCHAR(128) NOT NULL COMMENT 'bcrypt哈希，用于登录校验',
  `init_password_enc` VARCHAR(255) DEFAULT NULL COMMENT 'AES加密的初始密码，过期置空',
  `init_pwd_expire_at` DATETIME DEFAULT NULL COMMENT '初始密码可查看截止时间',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '1:启用 0:禁用',
  `remark` VARCHAR(255) DEFAULT NULL COMMENT '导入备注',
  `failed_login_count` INT NOT NULL DEFAULT 0 COMMENT '连续登录失败次数',
  `locked_until` DATETIME DEFAULT NULL COMMENT '账号锁定截止时间',
  `last_login_at` DATETIME DEFAULT NULL COMMENT '最后登录时间',
  `last_login_ip` VARCHAR(45) DEFAULT NULL COMMENT '最后登录IP',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_account` (`account`),
  KEY `idx_name` (`name`)
) ENGINE=InnoDB COMMENT='考生账号表';

-- 2. 题目表 (questions - 补齐需求文档4.2缺失细节)
CREATE TABLE `questions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `code` VARCHAR(32) NOT NULL COMMENT '题号标识，如 Q1, Q2（用于下载文件命名）',
  `title` VARCHAR(128) NOT NULL COMMENT '题目名称',
  `description` TEXT NOT NULL COMMENT '题目具体要求',
  `ref_image_key` VARCHAR(255) DEFAULT NULL COMMENT '参考图对象存储Key',
  `prompt_examples` JSON DEFAULT NULL COMMENT '3条提示词示例 JSON数组: ["示例1","示例2","示例3"]',
  `default_quota` INT NOT NULL DEFAULT 2 COMMENT '该题默认生成次数',
  `sort_order` INT NOT NULL DEFAULT 0 COMMENT '排序权重，越小越靠前',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '1:启用 0:停用(软删除)',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code` (`code`),
  KEY `idx_status_sort` (`status`, `sort_order`)
) ENGINE=InnoDB COMMENT='考试题目表';

-- 3. 配额表 (quotas - 并发防超发核心表)
CREATE TABLE `quotas` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `candidate_id` BIGINT UNSIGNED NOT NULL,
  `question_id` BIGINT UNSIGNED NOT NULL,
  `quota_total` INT NOT NULL DEFAULT 2 COMMENT '分配总次数',
  `quota_used` INT NOT NULL DEFAULT 0 COMMENT '已用次数（含生成中）',
  `adjusted_flag` TINYINT NOT NULL DEFAULT 0 COMMENT '是否被管理员手动调整过 1:是 0:否',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_candidate_question` (`candidate_id`, `question_id`),
  KEY `idx_question` (`question_id`)
) ENGINE=InnoDB COMMENT='考生题目配额表';

-- 4. 开放时段表 (time_windows)
CREATE TABLE `time_windows` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(64) NOT NULL COMMENT '时段名称，如：上午场',
  `start_at` DATETIME NOT NULL COMMENT '开始时间',
  `end_at` DATETIME NOT NULL COMMENT '结束时间',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '1:启用 0:停用',
  `remark` VARCHAR(255) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_active_window` (`status`, `start_at`, `end_at`)
) ENGINE=InnoDB COMMENT='开放时段配置表';

-- 5. 模型引擎配置表 (engines)
CREATE TABLE `engines` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `code` VARCHAR(32) NOT NULL COMMENT '对外代号，如 wq-v1, wq-v1-lite',
  `name` VARCHAR(64) NOT NULL COMMENT '后台展示名称',
  `provider` VARCHAR(32) NOT NULL DEFAULT 'minimax' COMMENT '供应商标识',
  `model_name` VARCHAR(64) NOT NULL COMMENT '上游真实模型名，如 MiniMax-H3-Highspeed',
  `resolution` VARCHAR(16) NOT NULL DEFAULT '768P' COMMENT '768P / 480P',
  `duration` INT NOT NULL DEFAULT 15 COMMENT '固定时长(秒)',
  `price_per_sec` DECIMAL(10,4) NOT NULL DEFAULT 0.0000 COMMENT '单价(元/秒)',
  `max_concurrency` INT NOT NULL DEFAULT 25 COMMENT '服务端最大并发上限',
  `is_default` TINYINT NOT NULL DEFAULT 0 COMMENT '是否为默认主引擎 1:是 0:否',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '1:启用 0:停用',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code` (`code`)
) ENGINE=InnoDB COMMENT='视频生成模型配置表';

-- 初始化默认主备引擎（对齐需求5.3与6.3）
INSERT INTO `engines` (`code`, `name`, `provider`, `model_name`, `resolution`, `duration`, `max_concurrency`, `is_default`, `status`) VALUES
('wq-v1', '标准高清引擎(768P)', 'minimax', 'MiniMax-H3-Highspeed', '768P', 15, 25, 1, 1),
('wq-v1-lite', '极速备用引擎(480P)', 'minimax', 'MiniMax-H3-Lite', '480P', 15, 25, 0, 1);

-- 6. 作品与生成任务表 (works - 你与同伴的核心交接表)
CREATE TABLE `works` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `task_no` VARCHAR(64) NOT NULL COMMENT '对外任务号(UUID/雪花ID)，防爬虫遍历',
  `candidate_id` BIGINT UNSIGNED NOT NULL,
  `question_id` BIGINT UNSIGNED NOT NULL,
  `seq` INT DEFAULT NULL COMMENT '该生在该题下的第几次提交(1, 2...)；draft 阶段为空，扣减配额成功时写入',
  `image_key` VARCHAR(255) NOT NULL COMMENT '首帧图对象存储Key',
  `aspect_ratio` VARCHAR(16) DEFAULT '16:9' COMMENT '首帧画幅比例',
  `prompt` VARCHAR(500) NOT NULL COMMENT '提示词(≤300字)',
  `task_status` VARCHAR(20) NOT NULL DEFAULT 'draft' COMMENT 'draft:草稿 queued:排队中 running:生成中 succeeded:可下载 failed:失败',
  `engine_code` VARCHAR(32) DEFAULT NULL COMMENT '实际使用的引擎代号',
  `upstream_task_id` VARCHAR(128) DEFAULT NULL COMMENT 'MiniMax返回的云端TaskID(对账用)',
  `video_key` VARCHAR(255) DEFAULT NULL COMMENT '成片MP4对象存储Key(独立持久)',
  `file_size` BIGINT DEFAULT NULL COMMENT '成片字节大小',
  `duration_sec` INT DEFAULT 15 COMMENT '成片实际秒数(用于计费统计)',
  `cost_seconds` INT DEFAULT NULL COMMENT '端到端生成耗时(秒)',
  `retry_count` INT NOT NULL DEFAULT 0 COMMENT '内部已重试次数',
  `quota_refunded` TINYINT NOT NULL DEFAULT 0 COMMENT '失败后是否已退还配额 1:已退 0:未退',
  `error_code` VARCHAR(64) DEFAULT NULL COMMENT '内部错误码',
  `error_msg` VARCHAR(255) DEFAULT NULL COMMENT '展示给考生的可读失败原因',
  `submitted_at` DATETIME DEFAULT NULL COMMENT '扣减配额并入队时间',
  `finished_at` DATETIME DEFAULT NULL COMMENT '进入终态(成功/失败)时间',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_no` (`task_no`),
  KEY `idx_candidate_question` (`candidate_id`, `question_id`),
  KEY `idx_status_submitted` (`task_status`, `submitted_at`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB COMMENT='考生作品与生成任务表';

-- 7. 管理员表 (admins)
CREATE TABLE `admins` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(64) NOT NULL,
  `password_hash` VARCHAR(128) NOT NULL,
  `role` VARCHAR(32) NOT NULL DEFAULT 'admin' COMMENT 'admin / super_admin',
  `status` TINYINT NOT NULL DEFAULT 1,
  `last_login_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB COMMENT='后台管理员表';

-- 8. 审计与配额变更日志表 (audit_logs)
CREATE TABLE `audit_logs` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `admin_id` BIGINT UNSIGNED NOT NULL COMMENT '系统自动退还时可记为0',
  `admin_username` VARCHAR(64) NOT NULL COMMENT '冗余操作人账号，防删号丢失',
  `action` VARCHAR(64) NOT NULL COMMENT '动作类型: import_users / reset_pwd / adjust_quota / refund_quota / extend_window / toggle_engine 等',
  `target_type` VARCHAR(32) NOT NULL COMMENT '对象类型: candidate / quota / time_window / engine / question',
  `target_id` VARCHAR(64) DEFAULT NULL COMMENT '目标ID或批次号',
  `before_val` JSON DEFAULT NULL COMMENT '变更前快照',
  `after_val` JSON DEFAULT NULL COMMENT '变更后快照',
  `remark` VARCHAR(255) DEFAULT NULL COMMENT '原因说明(如下调配额原因、失败退还原因)',
  `ip_address` VARCHAR(45) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_admin_time` (`admin_id`, `created_at`),
  KEY `idx_action_time` (`action`, `created_at`)
) ENGINE=InnoDB COMMENT='管理员关键操作与配额变动审计表';
