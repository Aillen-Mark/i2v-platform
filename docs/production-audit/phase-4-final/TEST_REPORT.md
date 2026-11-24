# Issue 008 收尾测试报告

原始第四阶段结果为 119 项通过，记录继续保留。[原始测试报告](../phase-4/TEST_REPORT.md)

本轮环境为 Windows、独立 MySQL 测试库、Redis DB 15、本机隔离 Moto S3 端点及真实 boto3 SDK、MySQL 命令行工具、FFmpeg/FFprobe。配置测试注入端点参数，检查 CLI 是否进入恢复流程。对象测试实际经 HTTP 读写随机命名的测试桶。联合恢复演练使用导入指定 dump 的新数据库。T69 将观察时间推进到测试对象的七天保护期之后。

所有 T57–T91 映射到下面第一条命令，表中名称为对应测试函数的主要标识。第二条覆盖既有 MiniMax 人工对账行为，第三条覆盖全量回归。

```bash
python -m pytest -p no:cacheprovider tests/test_object_operations.py -q -rA
python -m pytest -p no:cacheprovider tests/test_generation_attempts.py -q -rA
python -m pytest -p no:cacheprovider tests worker/test_worker.py worker/test_media.py -q -rA
```

| 执行 | 通过 | 失败 | 跳过 | 耗时 |
| --- | ---: | ---: | ---: | ---: |
| 对象存储定向 | 32 | 0 | 0 | 49.02 秒 |
| 既有人工对账回归 | 16 | 0 | 0 | 9.62 秒 |
| 完整后端回归 | 142 | 0 | 0 | 267.85 秒 |

本轮相对原始 119 项新增 23 个 Pytest 用例，覆盖 T57–T91。定向结果包含在全量结果中。Ruff、Python 语法、Git 空白和文档链接检查通过。

| 编号 | 用例 | 预期与运行断言 | 结果 |
| --- | --- | --- | --- |
| T57 | restore_cli_endpoint_security | 普通 HTTP 被拒，CLI 退出 3，没有进入恢复。 | PASS |
| T58 | restore_cli_endpoint_security | 合法 HTTPS 进入配置后的恢复入口，CLI 退出 0。 | PASS |
| T59 | restore_cli_endpoint_security | 显式演练允许本地 HTTP，演练标志传入恢复入口。 | PASS |
| T60 | real_backup_restore_and_seals | 联合快照演练落盘报告记录 LOCAL_DRILL。 | PASS |
| T61 | restore_cli_endpoint_security | 生产模式即使显式演练仍退出 3。 | PASS |
| T62 | restore_cli_endpoint_security | 拒绝回环地址和畸形端点，覆盖用户信息、非根路径、查询、片段及非法端口。 | PASS |
| T63 | checkpoint_classes_remain_read_only_across_pages | 有效租约的对象为 ACTIVE_CHECKPOINT。 | PASS |
| T64 | checkpoint_classes_remain_read_only_across_pages | 失效租约的检查点含世代、大小和摘要，分类为 STALE_CHECKPOINT。 | PASS |
| T65 | checkpoint_classes_remain_read_only_across_pages | Work 仍在运行时，过期租约的检查点仍为 ACTIVE_CHECKPOINT。 | PASS |
| T66 | checkpoint_classes_remain_read_only_across_pages | 成功 Work 与同 Key 检查点的摘要通过，归为 HEALTHY。 | PASS |
| T67 | checkpoint_classes_remain_read_only_across_pages | 成功引用只走作品校验，没有进入孤儿候选。 | PASS |
| T68 | checkpoint_classes_remain_read_only_across_pages | 失败 Work 残留、及成功 Work 对应的另一历史 Key 均归为 STALE_CHECKPOINT。 | PASS |
| T69 | checkpoint_classes_remain_read_only_across_pages | 已过观察期且无引用的对象为 ORPHAN_OBJECT。 | PASS |
| T70 | checkpoint_classes_remain_read_only_across_pages | 普通七天观察窗口将新对象归为 GRACE_PERIOD。 | PASS |
| T71 | checkpoint_classes_remain_read_only_across_pages | 一个 Key 被多个检查点认领，产生 REFERENCE_CONFLICT。 | PASS |
| T72 | reconciliation_reports_real_s3_and_mysql_connection_failures | MySQL 连接被拒，孤儿计数为零。 | PASS |
| T73 | checkpoint_classes_remain_read_only_across_pages | 扫描前后 Work 与 GenerationExecution 行一致。 | PASS |
| T74 | checkpoint_classes_remain_read_only_across_pages | 对象元数据及逐个回读摘要与扫描前一致。 | PASS |
| T75 | checkpoint_classes_remain_read_only_across_pages | 每段限定两条，游标继续到两侧结束。JSON 和 CSV 保留新分类。 | PASS |
| T76 | object_only_backup_recovery_and_cli | 对象复制成功且无归档时为 OBJECTS_COMPLETE。 | PASS |
| T77 | object_only_backup_recovery_and_cli | 同一批次一致性为 OBJECTS_ONLY。 | PASS |
| T78 | object_only_backup_recovery_and_cli | 实际调用 CLI，纯对象完成退出 4。 | PASS |
| T79 | real_backup_restore_and_seals | 有效 MySQL 归档和完整对象产生 COMPLETE + CONSISTENT。 | PASS |
| T80 | archive_failures_cannot_publish_common_recovery_point | 单个归档文件发布失败，报告 PARTIAL，目标没有批次 Seal 摘要。 | PASS |
| T81 | archive_failures_cannot_publish_common_recovery_point、real_backup_restore_and_seals | 本地归档损坏被拒，远端归档等长损坏也在恢复首条对象写入前被拒。 | PASS |
| T82 | backup_partial_unconfigured_and_conflicting_dedup | 部分对象复制失败，状态为 PARTIAL + INCONSISTENT。 | PASS |
| T83 | backup_partial_unconfigured_and_conflicting_dedup | 无目标返回 OFFSITE_NOT_CONFIGURED + NOT_APPLICABLE。 | PASS |
| T84 | seal_state_matrix | 即使重新计算 Seal 哈希，PARTIAL、FAILED 和缺 inventory_complete 都无法恢复。 | PASS |
| T85 | object_only_backup_recovery_and_cli | 正常恢复拒绝纯对象批次，目标 Key 仍不存在。 | PASS |
| T86 | object_only_backup_recovery_and_cli | 显式演练恢复成功，报告标记 LOCAL_DRILL_OBJECTS_ONLY。 | PASS |
| T87 | object_only_backup_recovery_and_cli | 即使显式演练，纯对象 Seal 也无法 fetch-database。 | PASS |
| T88 | seal_state_matrix | 单独改写 Seal 状态导致哈希不符，目标不写入。 | PASS |
| T89 | seal_state_matrix | 旧版伪造 CONSISTENT 和数据库关联残缺均被拒，旧纯对象证据只能用于显式演练。 | PASS |
| T90 | real_backup_restore_and_seals | 独立库和对象桶联合恢复，验证归档并抽样完整解码。 | PASS |
| T91 | real_backup_restore_and_seals、object_only_backup_recovery_and_cli | 已恢复对象从测试端点回读，SHA-256 和大小均匹配。 | PASS |

真实 MinIO、真实 S3 异地故障域和生产等价 HTTPS 恢复为 BLOCKED。配置注入验收不计为真实 TLS 联调，本地 S3 联合演练不计为异地恢复。
