# 对象对账

入口为 `python -m worker.object_reconciliation`。它只读 MySQL 和视频 Bucket，不修改 Work、Execution、Attempt、对象或退款。默认快速模式只做 Work 引用、HEAD 和大小检查。完整模式按块重算 SHA-256，默认并发 1、速率 256 KiB/s、有限总时长。

## 分类

- `HEALTHY`：有可信 Execution SHA-256，大小和完整读取摘要一致。
- `METADATA_MATCH`：快速模式大小一致，等待完整模式确认。
- `LEGACY_UNVERIFIED`：历史引用存在且对象大小一致，但无可信摘要。
- `MISSING_OBJECT`、`SIZE_MISMATCH`、`CHECKSUM_MISMATCH`：对应异常。
- `ACTIVE_CHECKPOINT`：活动租约或活动 Work 的检查点对象，保护期内不判孤儿。
- `ORPHAN_OBJECT`：过观察期、无 Work/Execution 引用的 generated 候选。Mock 与未知前缀标记为 `UNCLASSIFIED_OBJECT`。
- `REFERENCE_CONFLICT`：多个 Work 引用同一 Key，或 Work 与 Execution 检查点不一致。
- `GRACE_PERIOD`：generated 对象尚在默认 7 天观察期。
- `STORAGE_UNAVAILABLE`、`DATABASE_UNAVAILABLE`：服务错误，绝不转换为缺失或孤儿。

报告会生成 JSON、CSV 和小型游标文件，可用 `--resume` 继续。JSON summary 记录扫描数、各分类数量、运行时间和最后游标。CSV 对可能被表格软件解释的前缀加单引号。CLI 退出码 0 表示无异常，1 表示发现对象异常，2 表示依赖不可用或中断，3 表示参数错误。

```bash
python -m worker.object_reconciliation --mode quick --dry-run --output reports/object-reconciliation
python -m worker.object_reconciliation --mode full --batch-size 100 --max-concurrency 1 --rate-kib 256 --resume reports/object-reconciliation/<run>.cursor.json
```


建议在运维定时任务里分开配置快速和完整扫描。快速扫描可每日运行，完整扫描放在非考试窗口。计划任务保留每段退出码和报告；负载预算由按机器资源和竞态实测再调整。游标内的 ContinuationToken 绑定同一 Bucket。S3 拒绝旧 token 或内容大量改动时，重新执行全扫描。Work 引用使用现有表索引和按页查询，大库存的 Key 反查成本需测量。

## 失效检查点

终止任务残留的检查点且未成为对应成功作品时，输出 `STALE_CHECKPOINT` 和计数 `stale_checkpoint_count`。活动 Work 即使租约过期仍受保护。详见[收尾对账分类](../phase-4-final/RECONCILIATION_CLASSIFICATION.md)。
