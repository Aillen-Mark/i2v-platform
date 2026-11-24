# Issue 008 收尾审计

基线为本地 `8956cdf`（`codex/i2v-worker`），工作树 `1e111e1`，审计开始时无未提交改动。本地无仓库 `.env`，当前 PowerShell 未注入对象存储或数据库配置。测试沿用隔离测试库与本机 S3 协议端点。

| 位置 | 实查结果 |
| --- | --- |
| 恢复 CLI | `configured_store("RESTORE", allow_local=True)` 绕过正常恢复的 HTTPS 限制，报告没有演练模式字段。 |
| 检查点对账 | `object_claims()` 只返回活动标志等部分字段。过期且没有活动 Work 的检查点进入 `key in links` 分支，扫描跳过。 |
| 备份状态 | 无数据库快照时，成功复制会写 `COMPLETE + INCONSISTENT`。旧 Seal 验证依赖状态和两个计数，缺少清单完成断言及归档描述校验。 |
| 已有防护 | 存量条件写入、目标回读校验及下载 API 保留。现有人工对账已经拒绝 MiniMax 的 failed/cancelled。 |

按恢复配置、检查点分类、备份语义依次补强，再运行定向与完整回归。历史 Seal 只读解析，通过版本和完整性校验决定能否使用。
