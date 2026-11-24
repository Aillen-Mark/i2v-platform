# Issue 008 实施报告

| 文件 | 修改原因 |
| --- | --- |
| `backend/app/core/storage.py`、`object_integrity.py` | 保留 boto3 单例，增加显式超时、分页元数据、条件写入、存在性判断和流式 SHA-256。 |
| `backend/worker/artifacts.py` | 复用流式校验，保持 009 上传前检查点、上传后验真和租约。 |
| `backend/worker/object_inventory.py` | 分批列出成功 Work，关联执行检查点，检测活动对象和多作品引用。 |
| `backend/worker/object_transfer.py` | 共享传输预算、流式复制和不覆盖条件。 |
| `backend/worker/object_reconciliation.py` | 只读对账、分类、报告、恢复游标和退出码。 |
| `backend/worker/object_backup_manifest.py`、`object_backup.py` | MySQL 快照验证、JSONL 清单、内容寻址备份、密封及恢复。 |
| `backend/worker/generation_reconciler.py` | 拒绝将 MiniMax failed/cancelled 绑定为 accepted。 |
| `backend/tests/test_object_operations.py`、`test_generation_attempts.py` | 对账、失败备份、隔离恢复、MiniMax 终态回归。 |
| `docs/production-audit/phase-4/`、`docs/progress.md` | 审计、部署、使用、风险和测试记录。 |

没有数据库迁移。原有作品 API、视频 Key、CDN 替换、Range 和 009 成片验真均继续使用原有公开契约。

上传前 GenerationExecution 写入 Key、大小和 SHA-256。009 校验 MP4，上传后流式回读；写库失败时下一有效世代仍可读取检查点并重新做媒体验真后提交。新对账识别缺失、长度错配、同长度损坏、活跃检查点和超过 7 天且无引用的孤儿候选。历史对象缺可信摘要会被单列，完整模式计算值仅写报告。共享 Key 会输出引用冲突。

备份按已成功作品数据库清单，校验原件并按 SHA-256 去重，上传后完整回读。恢复仅向隔离目标条件写入，现有同名冲突会停止。005 的压缩 dump、清单及密封形成共同恢复点。工具验证快照格式、归档摘要、目标是不同的数据库名以及无活动 Work。操作者必须保证该隔离库确实导入指定快照并保持只读。恢复时逐行核查该库的成功作品与对象清单。

```bash
python -m worker.object_reconciliation --mode quick --dry-run --output reports/object-reconciliation
python -m worker.object_reconciliation --mode full --batch-size 100 --max-concurrency 1 --rate-kib 256
python -m worker.object_backup backup --output reports/object-backup
```

备份目标由环境配置，未配置返回 OFFSITE_NOT_CONFIGURED。隔离 S3 测试覆盖备份、清单和恢复。全量回归 119 项通过，0 失败。最终对象相关用例复测 9 项通过。真实 MinIO、签名过期和异地故障域仍待验证。测试结果、命令与逐项证据见[测试报告](TEST_REPORT.md)，共同恢复顺序见[运维手册](BACKUP_RESTORE_RUNBOOK.md)。

下一阶段启动前应先在计划的 MinIO 版本和独立目标上验证条件 PUT、签名有效期、端到端恢复和 5 Mbps 下的资源预算。Issue 006 留待单独启动。

## 收尾版本

收尾增加 HTTPS 门禁、失效检查点及备份完成级别，使用时以[收尾实施报告](../phase-4-final/IMPLEMENTATION_REPORT.md)为准。
