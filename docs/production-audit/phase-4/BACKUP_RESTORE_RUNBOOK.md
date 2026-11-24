# 运维手册

## 备份共同恢复点

1. 暂停入口接收新生成、Mock、管理写入和对象生命周期操作。让生成、Dispatcher 与恢复任务排空，再停止生成 Worker、恢复 Worker、Dispatcher、Beat。查看进程管理器和队列状态确认没有上传进程。用只读 SQL 确认活动与有效租约归零。若不归零，推迟共同快照。
   ```sql
   SELECT task_status, COUNT(*) FROM works
    WHERE task_status IN ('queued', 'running') GROUP BY task_status;
   SELECT COUNT(*) FROM generation_executions
    WHERE lease_owner IS NOT NULL AND lease_expires_at > UTC_TIMESTAMP(6);
   ```
2. 用 005 工具备份并在空的隔离数据库恢复该份归档。保存源库信息与恢复证据。维持源视频 Key 与对象不变直到备份结束。
   ```bash
   bash deploy/ubuntu/backup.sh /etc/i2v/mysql-backup.cnf i2v_platform /var/backups/i2v
   bash deploy/ubuntu/restore.sh /etc/i2v/mysql-isolated.cnf /var/backups/i2v/<snapshot> i2v_backup_inventory --create-new
   ```
3. 在 `backend/` 以隔离库存储清单账号运行。将 `MYSQL_DATABASE` 指向只读隔离清单库，MinIO 配置仍指向有源对象的只读账号。配置独立 S3 备份目标以及可审计的故障域标识，输出目录应在受保护分区。
   ```bash
   python -m worker.object_backup --rate-kib 192 backup --output /var/backups/i2v/objects --database-snapshot /var/backups/i2v/<snapshot> --confirm-database-restored
   ```
4. 核对 COMPLETE、CONSISTENT、清单与备份 ID、对象数量及最后成功对象。故障中断或目标不可用时保留记录，可重跑新批次，按完整内容验证跳过已存在对象。完成操作记录后恢复业务。

## 隔离恢复演练

1. 用同一备份目标和前缀取回已校验的 MySQL 归档。目标路径必须尚未存在。
   ```bash
   python -m worker.object_backup fetch-database <BACKUP_ID> --destination /var/backups/i2v/restore-drill
   bash deploy/ubuntu/restore.sh /etc/i2v/mysql-isolated.cnf /var/backups/i2v/restore-drill/<snapshot> i2v_object_restore --create-new
   ```
2. 配置工具连接刚恢复的隔离数据库。新建专用的恢复 Bucket，配置 `I2V_RESTORE_S3_ENDPOINT`、`I2V_RESTORE_S3_BUCKET`、`I2V_RESTORE_S3_REGION`、`I2V_RESTORE_S3_ACCESS_KEY`、`I2V_RESTORE_S3_SECRET_KEY`。确认其允许有条件写入并能读回。本地对象演练令全局参数 `--local-drill` 生效，正式演练不使用该开关。
   ```bash
   python -m worker.object_backup restore <BACKUP_ID> --output /var/backups/i2v/restore-report --confirm-isolated-target --media-samples 1
   ```
3. 确认恢复报告为 COMPLETE，MySQL 成功作品和清单一一相符。抽取实际测试下载，检查文件完整、FFprobe 和 FFmpeg 完整解码均通过。用隔离应用检查签名 URL、Range、前端预览和登录。生产切换属于后续业务操作。

不完整备份或摘要错配停止恢复。退出码 2 表示失败，3 表示未配置或参数错误。恢复前备份首次强制检查当前连接配置，恢复到不同的出片桶。回滚可直接停止新命令并保留已有对象、数据库及报告。
