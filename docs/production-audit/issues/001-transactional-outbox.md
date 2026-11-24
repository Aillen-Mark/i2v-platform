# 001 提交事务与持久化任务派发

**优先级** P0
**审查结论** 已确认，静态审查。原子扣额和同 Work 重提保护已存在。
**发布状态** 待批准

## 问题背景

考生提交需要一次性扣额，同时保证已确认的任务有可恢复的执行入口。当前 MySQL 和 Celery 之间没有持久化交接。

## 实际代码依据

`submit_work()` 锁 Work、按条件更新配额，在第 274 行提交，随后签名并派发 Celery。派发异常调用退额，但回调先执行 Redis `ZREM`。当前 SQL 只有 `works` 等业务表，无 Outbox 表。[提交](../../../backend/app/services/works.py#L179) · [退额](../../../backend/app/services/handoff.py#L199) · [SQL](../../../docs/schema.sql#L101)

## 风险说明

API 在提交与发布之间退出时，已扣额的作品可留在 `queued`。Redis 故障既能阻断发布，也能阻断补偿。发布成功但写发布确认前退出会造成重复消息。

## 改造目标

一个 MySQL 事务提交扣额、Work 状态和唯一 Outbox 事件。Dispatcher 至少一次发布；消费幂等。把 Redis 排队标记降为可重建的展示数据。

## 涉及文件

`backend/app/services/works.py`、`handoff.py`、`handoff_sync.py`、`backend/app/models/`、`backend/worker/tasks.py`、待审查的迁移目录及独立 Dispatcher 入口、`backend/tests/`。

## 修改范围

新增版本化事件、唯一键、下次重试时间和认领租约。批量认领后提交 MySQL，再发 Celery，再以租约条件标记已发布。采用有界退避和积压告警。以已发布但长期无进展的 Work 做补派。保留一次退款的行锁，并校验存在有效扣额；缓存清理失败不得阻止结算。

## 明确不修改的内容

外部模型请求对账属 002。预存量清理、前端页面重做、下载和转码策略不纳入本单。

## 数据库影响

加表与索引，不批量改历史记录。若增加扣退流水，使用每 Work 每类动作唯一键，评审明确存量基线和审计兼容。旧 `queued` 先只读分类，经批准后才迁移或恢复。

## 兼容性要求

保持现有 HTTP 路径、字段及考生端可识别状态。业务语义变更需在设计审查说明：成功响应确认的是持久接收，派发失败进入后台恢复。过渡期保证消息格式兼容，所有进程只使用一条权威发布路径。

## 测试要求

真实 MySQL、Redis 和 Celery。注入提交后 API 退出、Redis 不可用、发布前及发布后未确认时 Dispatcher 退出。并发重提同 Work、重复发布、最后一次额度竞争、重复补偿。

## 验收标准

- [ ] 成功响应对应唯一持久事件、唯一扣额和可追溯记录。
- [ ] 依赖恢复后积压在批准的时限内补派，重复消息无额外扣额或退款。
- [ ] Broker 已接收但消息丢失可从 MySQL 找回，积压告警可核对。

## 回滚方案

停新提交，停止新旧发布进程的并行切换。盘点 Outbox、Broker 和活动 Work，排空或按审批保留事件。回退应用版本时保留新增表和事件，防止重新发布已消费事件。

## 依赖的其他 Issue

无硬依赖。005 的备份和恢复演练是生产迁移门禁。与 002、007 一起审查消费者幂等和升级顺序。

## 实施设计

真实路径的 MySQL 事务写入 Work、配额、扣减审计和唯一 Outbox 记录。Work 继续使用 queued 表示平台已持久接收。Dispatcher 使用 InnoDB SKIP LOCKED 和可过期发布租约，按 Work ID 发布，不随消息发送图片签名链接或提示词。已发布但未被 Worker 确认的记录到期后再次派发。Worker 在获得执行槽后写入数据库收件记录，并忽略重复交付。数据库终态与退额先提交，随后尽力清理 Redis。

本单增加一张表和版本化 SQL，更新初始化 Schema。现有 Mock 联调继续走原接口。旧 queued/running 数据不回填、不自动重投。已进入 Worker 的上游调用、结果未知和租约接管由 002/007 扩展。HTTP 字段及前端保持兼容，真实提交响应确认的是平台持久接收。独立 Dispatcher 的生产接入在 004，先提供本地启动方式。

发布前暂停接单和 Worker，应用 SQL，启用兼容的 Worker 与 Dispatcher，再切换 API。回滚时暂停收件、盘点已接受且未确认的记录，保留表和事件，只在明确处理完活动任务后回到旧入队路径。

## 实施记录

真实提交在同一事务中写 Work、Quota、扣减审计和 Outbox。独立 Dispatcher 使用发布租约和至少一次投递，消息只传 Work ID 与类型标记。发布后的收件超时可补派。Worker 获得执行槽后记录收件，全量重复投递时以持久收件和原执行锁去重。Redis 队列标记只用于展示，结算不会被其清理失败阻断。

- 定向命令 `cd backend && python -m pytest -p no:cacheprovider tests/test_generation_outbox.py -q`，4 项通过。使用本机真实 MySQL、Redis 和 Celery，验证真实 Broker 连接失败、缺收件的补派、租约超时与过期发布者拒绝、一笔扣额及退还、插入失败原子回滚。
- 版本 SQL 在独立 MySQL 8.0 库运行两次，主键及两个索引符合预期。未迁移演示库或生产库。
- 全量 `python -m pytest -p no:cacheprovider tests worker/test_worker.py -q`，57 项通过。S3 使用 Moto 测试服务；未使用真实 MiniMax 或目标生产服务器。
- 剩余边界：测试用过期租约模拟派发边界，尚未执行实际 Dispatcher 或 Worker 强杀。Worker 已收件后的崩溃恢复、上游创建结果未知、并发历史数据调整仍需 002/007 和交叉验收。Redis 中会话故障仍会影响新请求的身份认证。
