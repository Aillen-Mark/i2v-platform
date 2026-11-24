# i2v-platform 生产稳定性改造

本批交付为本地审计和 Issue 档案。发布目标是 [caitomato/i2v-platform](https://github.com/caitomato/i2v-platform)。业务实施与线上 Issue 发布各自等待明确批准。

## 文件

- [审计报告](audit-report.md)
- [现有架构](architecture.md)
- [十个本地 Issue](issues/)
- [实施计划](implementation-plan.md)
- [Issue 依赖图](dependency-graph.md)
- [验收标准和证据状态](acceptance-criteria.md)

## GitHub Issue 发布草稿

| 状态 | 判定 |
|---|---|
| 待审查 | **当前状态**。核对本地 Markdown 中的证据、范围、验收和依赖。 |
| 待批准 | 审查完成，等用户明确批准哪些 Issue 可以发布。 |
| 待发布 | 已批准，等待校验目标、去重和按依赖顺序发布。 |
| 已发布 | GitHub 返回 Issue 链接后逐条登记真实编号与 URL。当前数量 0。 |

本地 Git 的 `origin` 指向 `Aillen-Mark/i2v-platform`。当前认证账号 `caitomato` 和目标 Fork 的 `main` 已用 GitHub API 只读核验。当前 PATH 中无 `gh`，因此无法运行 `gh auth status`。后续如使用 CLI，先核对 `gh auth status` 和 `git remote -v`，发布时显式指定 `--repo caitomato/i2v-platform`，避免把操作落到原仓库。

发布批次按实施计划。用户批准后，先读取目标仓库现有 Issues 并去重，然后用各 Markdown 的标题和正文逐条发布。先发布 blocker，再用真实 Issue 链接填入后续依赖。标签以目标仓库已有标签为准；本地的 001–010 是档案编号。批准实施某个 Issue 不等于批准发布 GitHub Issue。

## 第一批实施记录

- [003](issues/003-production-security.md) 本地实现及回归完成，实际 HTTPS 网关和审核厂商待目标环境验证。
- [005](issues/005-mysql-durability.md) 耐久参数和备份还原脚本已交付，真实隔离 MySQL 演练通过。异地挂载、调度、保留、Binlog 持续归档和 PITR 待环境验证。
- [001](issues/001-transactional-outbox.md) 本地提交、Dispatcher、迁移与故障定向验证完成。真实模型请求和执行期恢复落到 002/007。
