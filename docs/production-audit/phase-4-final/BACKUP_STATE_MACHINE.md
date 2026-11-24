# 备份状态和恢复资格

| status | consistency_status | CLI | 语义 |
| --- | --- | --- | --- |
| `RUNNING` | `INCONSISTENT` | — | 正在准备或复制 |
| `OBJECTS_COMPLETE` | `OBJECTS_ONLY` | 4 | 对象和清单已验证，允许显式对象演练 |
| `COMPLETE` | `CONSISTENT` | 0 | 已验证对象、清单、MySQL 归档及共同恢复点材料 |
| `PARTIAL` | `INCONSISTENT` | 2 | 至少有对象已验证，批次仍存在失败 |
| `FAILED` | `INCONSISTENT` | 2 | 批次失败 |
| `OFFSITE_NOT_CONFIGURED` | `NOT_APPLICABLE` | 3 | 未配置备份目标 |

配置错误退出 3。成功恢复退出 0，另以 `restore_mode` 表示演练类型。正常恢复和 `fetch-database` 要求共同恢复点。状态对、清单完成标志、整数计数、归档 ID 和时间、文件描述及大小、SHA-256 都要通过校验。联合恢复还会逐个读回归档文件核对摘要。

新批次使用版本 2，Seal 和 JSONL 记录均携带版本。旧版 `COMPLETE + CONSISTENT` 要通过同一套清单、归档描述和回读校验。旧版 `COMPLETE + INCONSISTENT` 仅在数据库关联字段全为空且通过其他完整性校验时允许显式对象演练，原状态保持不变。已发布批次不会被重写。
