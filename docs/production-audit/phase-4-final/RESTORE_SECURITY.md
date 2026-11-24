# 恢复安全边界

普通恢复和备份端点使用 HTTPS。URL 只能包含 scheme、主机与可选端口，路径仅允许空路径或根路径。拒绝用户信息、查询、片段以及正式模式下的回环地址。普通模式中，标记为本地演练的备份也不可作为来源。

本地 HTTP 通过全局参数 `--local-drill` 显式启用。联合快照演练的报告记录 `LOCAL_DRILL`，纯对象演练记录 `LOCAL_DRILL_OBJECTS_ONLY`。恢复始终要求隔离环境和显式确认目标，生产配置会在写入之前被拒绝。目标桶需与原桶、应用桶和备份桶隔离。

恢复先验证 Seal SHA-256、状态、计数和对象清单摘要。一致性批次还会在首次写入前读取归档并验证 SHA-256。随后对照隔离库，按条件创建目标对象并回读。目标同名而内容不同会停止。演练沿用上述完整性和写入约束。

```bash
python -m worker.object_backup restore BACKUP_ID --output reports/restore --confirm-isolated-target
python -m worker.object_backup --local-drill restore BACKUP_ID --output reports/drill --confirm-isolated-target
```
