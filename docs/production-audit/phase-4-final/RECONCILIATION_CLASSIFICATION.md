# 对象对账分类

| 情况 | 分类 |
| --- | --- |
| 一个 Key 被多个 Work 认领，或与生成 Key 内的 Work ID 矛盾 | `REFERENCE_CONFLICT`，优先报告 |
| 检查点租约仍有效，或对应 Work 仍是 queued/running | `ACTIVE_CHECKPOINT` |
| 过期或无租约，且未被同一成功 Work 作为成片引用 | `STALE_CHECKPOINT` |
| 成功 Work 的成片 Key 与同 Work 检查点相同 | 由成功作品扫描执行元数据或 SHA-256 校验 |
| 其他普通业务引用 | 由该引用继续保护 |
| 可识别的生成 Key，无 Work/Execution 引用及活动归属，且超过观察期 | `ORPHAN_OBJECT` 候选 |
| 同上，仍在观察期 | `GRACE_PERIOD` |
| Mock、静态或无法确认的 Key | `UNCLASSIFIED_OBJECT` |
| MySQL 失败 | `DATABASE_UNAVAILABLE`，中止后续孤儿判定 |

Work 和 Execution 按对象页批量查询。结构化检查点记录尺寸、摘要、Work 状态、Task No、执行世代、租约截止和可恢复时间。租约所有者仅用于判定，报告省略该令牌。JSON、CSV 和摘要计数都包含 `STALE_CHECKPOINT`。该项返回对账异常退出码 1。不同扫描阶段可能各自记录同一个引用冲突。已完成页可以沿用现有游标继续扫描。

对账只读。候选孤儿和失效检查点均保留现有对象、引用和任务状态。
