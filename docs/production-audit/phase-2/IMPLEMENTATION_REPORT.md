# 第二阶段实施报告

工作区以 `codex/i2v-worker` 的 `62f641f` 为基线。Issue 002 将上游请求结果持久化，Issue 007 使执行权、任务接管和成片检查点由 MySQL 控制。Work API 仍使用原有请求、响应和五种公开状态。配额扣减仍与 Work 和 Outbox 同一个事务。此次改动保留在本地工作区，数据库变更仅在隔离测试库验证。

## 文件及用途

| 文件 | 修改原因 |
| --- | --- |
| `backend/app/main.py` | 真实生成模式启动时检查新增表是否存在。 |
| `backend/app/models/__init__.py` | 注册两张表对应的 ORM 模型。 |
| `backend/app/models/generation_attempt.py` | 定义上游请求尝试、终态和对账时间。 |
| `backend/app/models/generation_execution.py` | 定义租约、执行世代及对象检查点。 |
| `backend/app/services/execution_lease.py` | 统一 Work 行锁、MySQL 时钟和关键写入的所有权检查。 |
| `backend/app/services/generation_outbox.py` | 提供尚未收件的事件积压和最老等待时间。 |
| `backend/app/services/handoff.py` | 拒绝无租约的异步回调覆盖受持久化执行保护的 Work。 |
| `backend/app/services/handoff_sync.py` | 同事务校验租约与尝试、更新终态与退款，使用现有额度字段和审计。 |
| `backend/worker/config.py` | 集中设置心跳、租期和已知任务人工核对阈值。 |
| `backend/worker/minimax.py` | 分类明确连接失败、拒绝和提交结果不明，检查返回的任务标识。 |
| `backend/worker/attempts.py` | 在 Work 行锁内检查合法转换，持久化 POST 前标记、接受凭证和对账时间。 |
| `backend/worker/execution.py` | 原子领取 Outbox、获取和续租、预留恢复派发。通过持久化开始时间保留接管前的耗时。 |
| `backend/worker/generation_reconciler.py` | 定期扫描未知结果，记录人工核对告警，支持录入已独立查证的任务 ID。 |
| `backend/worker/artifacts.py` | 持久化上传检查点，读回对象并核对长度和 SHA-256。 |
| `backend/worker/pipeline.py` | 按持久化状态继续提交、查询、上传或结算，接管沿用已知上游任务。 |
| `backend/worker/state.py` | 从 MySQL 判定恢复候选，保留 Redis 短锁和可续期并发槽。 |
| `backend/worker/tasks.py` | 将耗时任务与恢复扫描分队列，将短时 Celery 重试与数据库恢复区分。 |
| `backend/worker/outbox_dispatcher.py` | 输出 Outbox 积压日志，维持未收件事件补派。 |
| `backend/worker/README.md` | 提供四个进程的启动命令、配置、迁移与人工核对方式。 |
| `backend/tests/conftest.py` | 在独立测试库初始化和清理新增表。 |
| `backend/tests/test_generation_attempts.py` | 验证不同请求故障、未知结果隔离、人工核对和退额。 |
| `backend/tests/test_generation_recovery.py` | 验证子进程强杀、租约竞争、Redis 实际重启、连接失败、历史兼容和对象恢复。 |
| `docs/migrations/002_generation_attempts.sql` | 给现有数据库增加上游尝试表。 |
| `docs/migrations/003_generation_executions.sql` | 给现有数据库增加执行租约和检查点表。 |
| `docs/schema.sql` | 为新库同步两张表。 |
| `docs/production-audit/phase-2/` | 保存本轮审计、状态图、实施计划、风险、002 验证记录与本报告。 |

## 数据库结构与兼容性

- `generation_attempts` 包含主键 `id`、`work_id`、`attempt_no`、`request_id`、`provider`、`provider_task_id`、`status`、`retry_create`、`request_started_at`、`response_received_at`、`last_checked_at`、`next_check_at`、`error_code`、`created_at`、`updated_at`。唯一索引为 `uk_attempt_work_no(work_id, attempt_no)`、`uk_attempt_request(request_id)`、`uk_attempt_provider_task(provider, provider_task_id)`。任务 ID 可空，检查索引为 `idx_attempt_due(status, next_check_at)`。错误信息仅保存固定分类值。
- `generation_executions` 以 `work_id` 为主键，包含 `lease_owner`、`lease_generation`、`lease_expires_at`、`last_heartbeat_at`、`recover_after`、`started_at`、`legacy_uncertain`、`artifact_key`、`artifact_size`、`artifact_sha256`。扫描索引为 `idx_execution_recover(recover_after, lease_expires_at, work_id)`。
- 沿用现有 Work、Outbox 表风格，通过 ID 与事务保持关联。升级只建两张表。两份迁移在从基线初始化的一次性独立 MySQL 库连续执行两遍，历史 Work 整行相同。检查了索引字段顺序、三个唯一约束拒绝重复和空供应商 ID 可共存。测试后删除了该临时库。
- 已派发的历史活动任务首次恢复时，如果已有可信 MiniMax ID，则接续原任务。如果缺少可信 ID，则进入未知结果。尚未被领取的 Outbox 继续由 Dispatcher 派发；新 Worker 在实际领取时同时写入收件和租约。

## 任务恢复与退额

1. 每次 POST 必须先提交 `submitting`。接受后在同一事务保存供应商 ID、尝试状态与 Work。如果 Worker 在获取 ID 前死亡，或者已收到 ID 但没提交 MySQL，接管者把原尝试转为 `submission_unknown`。
2. MiniMax 文档确认可按供应商任务 ID 查询。公开创建接口未确认客户端幂等键，列表过滤无法证明某次本地请求与上游任务对应。未知结果约 15 分钟转人工核对，之后每小时提醒，扫描周期会带来额外等待。运营人员凭独立证据绑定精确任务 ID 后继续原任务。此边界存在需要人工处理的请求。[创建接口](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create) · [查询接口](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-query) · [列表接口](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-list)
3. 新租约在 Work 行锁内提交。执行世代单调递增，当前所有者、世代、有效期与 Work 状态共同约束关键写。默认租期 60 秒、心跳 15 秒。每个执行片都有硬截止，丢失心跳后停止推进副作用，后续由恢复扫描接管。
4. Outbox 未收件交给 Dispatcher，已收件的过期执行由恢复扫描处理。扫描通过 MySQL 预约下一次发布，单个 Work 的扫描发布间隔为两分钟。Celery 重投、连接恢复与 MySQL 接管可以并行发生，Work 行锁约束实际执行权。扫描默认每分钟运行一次，每轮最多 100 项。
5. 已知 ID 续查上游。单片截止释放执行机会。已知状态连续停滞超过配置阈值时交人工核对。确认上游已成功后，Worker 先检查对象记录和实际存储；可验证的对象复用，缺失时查询原任务并重新下载和处理。每个世代使用独立对象键。
6. 只有现有规则下的明确终态失败或已确认取消可以最终退额。明确且可重试的失败保留一次新尝试预算。失败状态、配额递减、`quota_refunded` 和审计一起提交。成功或已退款的 Work 接到重复消息时直接跳过。详见[状态机](TASK_STATE_MACHINE.md)。

## 测试与证据

测试环境为 Windows 主机和 WSL Docker。API 走真实 FastAPI ASGI 处理，事务走隔离 MySQL `i2v_platform_test`，Redis 使用独立逻辑库，T07 另启独立 Redis 7 Docker 容器。MiniMax 由本地可编程 HTTP 服务或 HTTP 测试传输模拟，对象存储由 Moto 的 S3 兼容协议模拟。表中应用日志可用 `--log-file` 复现。子进程停点、请求数量、事务和对象断言是故障证据的一部分。

| 编号 | 注入方式 | 预期与实测 | 日志或对应断言 | 结果 |
| --- | --- | --- | --- | --- |
| T01 | ASGI 子进程在生成 API 返回 200 后强杀 | Work、退额前余额与 Outbox 已提交，Dispatcher 仍可派发 | API 返回标记、进程退出码、Outbox 收件和真实 Redis 消息断言 | 通过 |
| T02 | Dispatcher 子进程在 Redis 发布成功、写回 MySQL 前强杀，测试将下一次派发推进到可领取 | 消息可再次派发，重复领取只保留一个租约 | 首次和再次发布消息、`execution_acquired`、`duplicate_delivery` | 通过 |
| T03 | Worker 完成尝试准备、准备首帧时强杀 | 新世代可安全发出首次 POST，上游 fixture 最终明确失败 | POST 计数从 0 到 1，`execution_acquired` 世代 2 | 通过 |
| T04 | HTTP fixture 接收 POST 后挂起响应并强杀 Worker。另测收到 ID 但写库前强杀 | 均进入未知，累计一次 POST，配额保持已用 | `generation_attempt status=submission_unknown code=SUBMISSION_INTERRUPTED`，Work、配额和请求数断言 | 通过 |
| T05 | 保存供应商 ID 提交后强杀 Worker | 接管者对原 ID 发 GET，POST 总数为 1 | HTTP fixture 调用路径、世代 2 和终态断言 | 通过 |
| T06 | 真实 Worker 执行子进程强杀，等待 MySQL 租期实到 | 过期后可接管，下一世代为 2 | 子进程退出、`execution_acquired takeover=True` | 通过 |
| T07 | 两次重启停用持久化的独立 Redis 容器，分别丢失未收件与已收件消息 | 未收件重新派发，已收件由扫描恢复且沿用原 ID | Redis 队列清空与重建、`execution_recovery_published`、世代 2 | 通过 |
| T08 | 四个线程竞争同一个 Work | 只有一个领取成功 | 唯一 lease 断言和 `duplicate_delivery` | 通过 |
| T09 | 租约过期且新世代获得所有权后，用旧世代提交写入 | 旧续租、尝试更新、对象写入、成功和退额均被拒绝 | `ExecutionStopped` 断言与新世代记录 | 通过 |
| T10 | 成功后再次发送失败回调并尝试重新领取 | 成功状态维持，未退款、未新增执行 | 回调幂等忽略、Work 及 POST 计数断言 | 通过 |
| T11 | 两次发送明确失败回调 | 只退一次，只有一条退额审计 | `refund_quota` 计数为 1、回调幂等忽略 | 通过 |
| T12 | 将未知尝试推进到对账时间，再模拟上游查询失败 | 进入人工核对，保留同一尝试与额度。独立核对成功后可绑定 | `Refund withheld`、`generation_manual_review`、`operator_verified_task` | 通过 |
| T13 | 对象写入并验证后、成功事务前强杀 Worker | 新世代复用已校验对象并落库成功，耗时保留前一执行段 | 对象哈希与长度校验、成功日志、Work 耗时与 POST 计数断言 | 通过 |
| T14 | 对象存储成功后，将最终写库连接指向关闭的本地 TCP 端口 | 实际连接失败时仍为运行中，恢复连接后可核对对象并成功 | `OperationalError`、首次未成功和随后成功断言 | 通过 |
| T15 | 并发恢复预约，另模拟重复扫描和重复收件消息 | 同一轮只产生一个有效恢复预约，只允许一个有效租约 | 恢复消息数、世代号、`execution_recovery_published` 与 `duplicate_delivery` | 通过 |

在 `backend/` 执行以下命令。通过独立实例配置提供 MySQL、Redis 与 S3 协议端点，Redis 重启测试需设置 `I2V_TEST_REDIS_DOCKER` 为本地 Docker 命令。本轮使用 `wsl -d Ubuntu -- docker`。

```bash
python -m pytest -p no:cacheprovider tests worker/test_worker.py -rA -q --log-file phase2-pytest.log --log-file-level INFO
```

- 全量回归 77 项通过，耗时 134.53 秒。涵盖原有 Outbox、Work API、配额和退款、生产安全配置、MiniMax 协议与历史任务兼容。Celery 生成任务入口测试还验证 MySQL 租约与真实 Redis 槽续期。删除测试槽后记录 `execution_heartbeat_lost`，任务停止推进。
- 迁移 SQL、变更 Python 文件的语法检查和 Ruff、Git diff 空白检查均已执行。阶段 002 的单独结果见[002 实施记录](ISSUE-002-IMPLEMENTATION.md)。
- T02 和 T12 通过直接推进库内时间条件缩短测试。T06 使用自然租期过期。T14 验证真实 TCP 连接拒绝，测试未停用 MySQL 实例。T13 验证对象字节完整性，播放与音轨验收属于 009。

## 可观测性与剩余风险

- Dispatcher 日志 `generation_outbox_backlog` 包含积压数和最老等待秒数。扫描日志 `generation_recovery_backlog` 包含未知、人工核对和活动 Work 的过期租约数量。
- `generation_attempt` 记录请求超时分类、尝试和已知任务标识。`provider_query_retry` 区分查询超时。可从 `execution_acquired` 的 `takeover`、`duplicate_delivery`、`execution_heartbeat_lost`、`execution_recovery_publish_failed` 和 `generation_incomplete` 分别统计接管、重复、续租和恢复异常。退额次数以数据库 `audit_logs.action=refund_quota` 为准。测试保留了签名 URL 脱敏回归。外部告警尚未接入。
- Redis 完全重启后，并发槽可能在心跳察觉前短暂偏离账号实际并发。MySQL 仍约束同一 Work 只能有一个有效租约。Celery 为至少一次派发。MiniMax 未知请求缺少可靠的自动关联接口，整体链路无法承诺 exactly-once。
- 现有管理员物理删除考生会删除其配额行。若其任务随后明确失败，退额事务回滚，需人工处理。世代隔离可能遗留未引用对象，由 008 处理清理与备份。
- 已知任务可能在成片链接过期、对象服务长期不可用或 FFmpeg 拒绝输入后进入人工核对。真实 MiniMax、MinIO、Linux prefork 与 Ubuntu 24.04 目标机验收尚未执行。T01 使用 ASGI 子进程，恢复任务的 Celery 入口在本地验证，生产监督进程、性能和容量尚待验证。

## 部署迁移与回滚

1. 暂停生成提交，等待旧 Worker 完成正在处理的任务或盘点尚未完成的 Work，停止旧 API、真实 Worker、Beat 和 Dispatcher。备份 MySQL 并核对恢复点。记录已收件、未收件、已知 MiniMax ID 及未知的旧任务。
2. 在目标应用库确认 001 已安装，依次执行 002、003。空库使用完整 schema。逐条检查迁移返回和表、索引；发现部分完成时按顺序重跑幂等建表 SQL。历史 Work 留待新恢复流程按需处理。
3. 使用同一版本启动生成 Worker、独立的恢复 Worker、Dispatcher、单实例 Beat 和 API。生产初始生成并发为 1，按目标机压测再调整。看过期租约、Outbox 最老等待、未知请求和对象错误，完成端到端验收后恢复提交。[进程启动命令](../../../backend/worker/README.md)
4. 回滚先暂停提交并停止新 Worker、扫描、Dispatcher 和 API，保留两张扩展表及对象。盘点 Outbox 和活动尝试，确认已知上游任务以及已写存储但未结算的作品，隔离未知请求并暂停遗留消费者接管。旧版消费者缺乏租约检查和未知保护。只有在全部活动任务安全结算、或经明确运维安排妥善隔离后，才启动旧消费者和 API。应用回滚无需删表；删除尝试、执行记录或对象会不可逆丢失对账与恢复证据。

下一阶段在目标机验证真实 MiniMax、MinIO 和 Celery prefork。随后由 Issue 006 验证 CPU 与并发预算、008 处理对象审计和清理、009 验证音轨及实际成片、010 扩展故障矩阵。
