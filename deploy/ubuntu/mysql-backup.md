# MySQL 备份与恢复

面向 Ubuntu 24.04、MySQL 8.0。使用官方 MySQL 一致的 `mysql` 和 `mysqldump` CLI，另需 Bash、GNU coreutils 和 gzip。运行账户仅授予备份或恢复所需权限。凭据写入本机专用的 MySQL client option file，以 `chmod 600` 限制读取。备份账户按表给予读取、视图、触发器和例程等必要权限；启用 Binlog 时，记录坐标还需要对应的 MySQL 权限。单独维护恢复账户和目标实例连接配置。

```bash
bash deploy/ubuntu/backup.sh /etc/i2v/mysql-backup.cnf i2v_platform /var/backups/i2v /mnt/i2v-offsite
bash deploy/ubuntu/restore.sh /etc/i2v/mysql-restore.cnf /mnt/i2v-offsite/<snapshot> i2v_restore_drill --create-new
```

## 备份执行

- 启动前确认备份目录有空间，异地地址指向另一个故障域的挂载。填不同路径只校验路径差异，并不能判定是否异地。优先使用加密的远端存储，将快照中的考生数据按运维权限隔离。
- 备份使用 InnoDB 一致性读；有非 InnoDB 基表就退出。避开迁移和 DDL。记录版本、服务器 UUID、实际刷盘设置及 Binlog 开关。启用 Binlog 时，`mysqldump --source-data=2` 记录坐标。
- 脚本使用私有临时目录，只在 dump、gzip 和校验均通过后公开本地快照；异地复制也在校验后原子发布。异地失败仍保留本地快照并返回非零。
- 004 应用定时调度，初始计划每日快照，收集退出状态、最近成功时间和远端剩余空间。建议起始值是本地 2 份、异地 14 份日快照，由运维确认后配置存储侧生命周期策略。脚本不删除已有快照。
- MinIO 备份由 008 实现。同步记录相同恢复点可取得的对象备份与 Fernet 原密钥。缺少原密钥会影响历史初始密码解密。

## 恢复与耐久

- 先沟通目标 RPO/RTO，使用隔离 MySQL 8.0 实例进行演练。脚本验证归档与哈希，只创建新库，已有目标库时失败，不覆盖原库。导入失败保留新库供排查。
- 恢复后核对候选人、作品、配额、审计的行数和抽样值。随后使用隔离应用检查登录、配额、待处理作品与成片下载。记录从故障模拟到可服务的时间。
- Compose 默认将 `innodb_flush_log_at_trx_commit` 与 `sync_binlog` 设为 1。设置前记录现有实例运行值、Binlog 增长量和检查点。生产以实测延迟评估 I/O，避免借调低耐久配置掩盖性能问题。
- PITR 需要远端连续归档 Binlog。脚本记录起始坐标并提供全量快照，暂未提供连续 Binlog 归档。演练应从快照位置使用 MySQL 8 对应版本的 mysqlbinlog 在隔离实例回放到指定时间或位置，确保回放不会触及源实例。归档周期和保留要覆盖预期恢复窗口。
- 盘点并验证旧的 `docs/schema.sql` 初始化实例再启用后续迁移。每个新版本的 Schema 迁移应独立、可重放并记录版本。回滚优先回到兼容应用版本并保留数据库卷，恢复数据应使用新实例验证后再切换。

当前仅本机 MySQL 8.0 隔离演练通过。Ubuntu 目标机器、真实异地挂载、持续归档及实际 RPO/RTO 仍待验证。
