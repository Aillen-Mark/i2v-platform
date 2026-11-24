# 备份与恢复设计

备份必须使用独立 S3 兼容目标。配置由环境变量提供：

`I2V_BACKUP_S3_ENDPOINT`、`I2V_BACKUP_S3_BUCKET`、`I2V_BACKUP_S3_REGION`、`I2V_BACKUP_S3_ACCESS_KEY`、`I2V_BACKUP_S3_SECRET_KEY`、`I2V_BACKUP_FAILURE_DOMAIN`、可选 `I2V_BACKUP_S3_PREFIX`。正式目标要求 HTTPS、非回环主机和独立故障域标识。缺少配置返回 `OFFSITE_NOT_CONFIGURED`，不会虚报完成。隔离演练使用 `--local-drill` 且必须使用不同 Bucket。

备份扫描成功 Work 的数据库引用，按源对象大小和 SHA-256 流式读取。备份对象按 SHA-256 内容寻址。目标已存在且摘要一致时跳过，摘要不一致时停止并报告，任何失败都使批次 PARTIAL/FAILED。对象清单是 JSONL。完整批次在目标写入清单、数据库归档、seal.json 与 seal.sha256 后才可被恢复工具接受。

共同恢复点需要人工维护窗口。先暂停生成提交和 Worker，等待活动上传结束，核对 `generation_executions.lease_expires_at` 和 `artifact_key`，执行 005 的 MySQL 快照，再使用该隔离快照的数据库连接生成对象清单。工具拒绝活动 Work，记录 MySQL 快照 ID、SQL 压缩包摘要、对象清单摘要、源和目标 Bucket、目标故障域与一致性状态。数据库和对象不是一个分布式事务，状态为 INCONSISTENT 时不能正常恢复。

恢复默认使用新 Bucket，禁止覆盖应用 Bucket 和生产配置。校验密封、清单摘要、数据库引用、目标对象大小和 SHA-256。目标同名对象摘要一致可记为已存在，摘要冲突会停止。至少随机抽取一个非 Mock 视频执行 FFprobe/完整媒体验真。


005 记录服务端 UUID、Binlog 开关、刷盘参数及启用 Binlog 时的坐标。该坐标可以用于隔离库的数据库重放。将数据库恢复到另外一个时刻后，必须另行证明所需对象的版本覆盖；本轮生成的清单只证明所记录的恢复点。每次运行生成不同的 backup_id，增量对象可被多个备份共享。不要按批次删除共享对象，也不要将各批次的归档和清单交叉组合。
