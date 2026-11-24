# 生产稳定性审计报告

本次以本地 HEAD `6096b60` 和干净工作树为基线。认证后只读核对 `caitomato/i2v-platform` 的 `main` 为 `d7c5ec4`，两者 tree 均为 `16dbf7105d5e79719e7528f5ecd2be781f63e31e`。逐项重新检查了提交与交接、配置、数据库与存储、Worker、Compose/Nginx、Schema、工作台及测试。此前 ZIP 快照未用作结论依据。

**首要问题是提交事务与执行交接之间的中断窗口。** 当前后端已实现同一 Work 的并发扣额保护、幂等退额和单条下载鉴权。缺口集中在跨服务恢复、媒体与对象验真、生产配置和部署。

## 十个 Issue 逐项复核

| Issue | 结论 | 当前实现和剩余缺口 |
|---|---|---|
| [001 Outbox](issues/001-transactional-outbox.md) | 已确认，静态 | MySQL 第 274 行提交，随后交给 Celery；不存在 Outbox 表。Redis 清理先于退额事务，Redis 故障可挡住退额。[提交](../../backend/app/services/works.py#L274) · [退额](../../backend/app/services/handoff.py#L199) |
| [002 MiniMax 请求](issues/002-minimax-persistence.md) | 部分已有，静态及单测 | 读超时当前无立即盲重发。请求和 ID 先记 Redis，数据库无持久尝试账本或未知态；跨 Redis 丢失无法安全对账。[提交标记](../../backend/worker/pipeline.py#L47) · [适配器](../../backend/worker/minimax.py#L33) |
| [003 生产安全](issues/003-production-security.md) | 已确认，静态 | Cookie 固定非 Secure；默认配置允许开发凭据和 Mock；Fernet 启动可回写；审核布尔值校验宽松；签名 URL 进入入队日志。[配置](../../backend/app/core/config.py#L34) · [会话](../../backend/app/core/sessions.py#L132) · [审核](../../backend/app/services/moderation.py#L35) |
| [004 Ubuntu 部署](issues/004-ubuntu-deployment.md) | 已确认，静态 | Compose 有基础设施健康检查，但缺 API、Worker、Beat、Dispatcher 服务。Nginx 从容器连宿主网关，配套 PowerShell 在 Windows 绑定环回口。[Compose](../../docker-compose.yml) · [Nginx](../../nginx/nginx.conf#L37) · [启动脚本](../../scripts/start_prod.ps1#L82) |
| [005 MySQL 持久性](issues/005-mysql-durability.md) | 已确认，静态 | 刷盘参数为 2。仓库缺迁移框架、异地备份和恢复脚本。实际目标主机的 Binlog 和备份状态未知。[Compose](../../docker-compose.yml#L10) · [连接池](../../backend/app/core/database.py#L23) |
| [006 资源隔离](issues/006-worker-resource-isolation.md) | 部分已有，静态 | 有 FFmpeg 线程限制及 Redis 并发槽。生成、轮询和转码在同一 Celery 任务执行，README 样例并发 25，容量未经目标规格实测。[任务](../../backend/worker/tasks.py) · [媒体](../../backend/worker/media.py#L34) · [命令](../../backend/worker/README.md#L24) |
| [007 Worker 恢复](issues/007-worker-recovery.md) | 部分已有，静态 | 晚确认、时限和 Beat 扫描已存在。锁和开始时间存 Redis，恢复与生成同队列；`queued` 超过两小时才入扫，届时无上游 ID 的任务会走过期退额。缺数据库所有权和完整阶段。[扫描](../../backend/worker/state.py#L113) · [过期行为](../../backend/worker/pipeline.py#L140) |
| [008 MinIO 一致性](issues/008-minio-consistency.md) | 部分已有，静态 | 单条下载 HEAD 已有；成片上传成功后直接落库，未经独立校验。列表按 DB 状态和对象键标记就绪。缺周期对账和异地对象备份。[上传](../../backend/worker/pipeline.py#L101) · [列表与下载](../../backend/app/api/works.py#L153) |
| [009 FFmpeg](issues/009-ffmpeg-reliability.md) | 已复现，本机实验 | 有超时和输出大小检查。实测目标 15 秒时，接受 1 秒有声输入；1 秒无音轨输入因固定映射音轨失败。缺时长、流信息验真。[媒体处理](../../backend/worker/media.py#L83) |
| [010 故障注入](issues/010-fault-injection.md) | 覆盖不足，静态及单测 | 业务用例已覆盖部分竞争、退款和下载。Worker 测试默认不收集。100 人脚本等待 Mock 成片，下载只测 Range 首块，缺真实上游和进程故障矩阵。[pytest 配置](../../backend/pytest.ini#L8) · [百人脚本](../../scripts/load_test_100.py#L140) |

## 已有保护和未复现项

- **同一 Work 基线重复扣额未见证据。** Work 行锁、非草稿的幂等分支及配额条件 UPDATE 已存在。现有测试含同 Work 重提与 20 并发争最后一次额度。本轮未重跑这两个数据库用例，也未把结论扩展到浏览器重新创建另一 Work。[提交服务](../../backend/app/services/works.py#L179) · [现有回归](../../backend/tests/test_m3_quotas_windows_gateway.py#L377)
- **常规重复退额已有保护。** 同事务锁 Work 及 Quota，按 `quota_refunded` 或成功状态忽略。仍需验证 Redis 故障、缺配额行和迟到 Worker。退款对终态的保护不等同于有扣额凭据的逐笔账本。[同步退额](../../backend/app/services/handoff_sync.py#L195)
- **MiniMax POST 读超时不立即盲重发。** 本轮单测重新确认。供应商已创建但结果丢失、Redis 丢失或 Worker 中断的进一步恢复本轮未执行。公开创建 API 未见文档化的客户端幂等键；查询依赖已知上游 ID，不能因此假定未知请求可自动找回。[现有测试](../../backend/worker/test_worker.py#L55) · [创建接口](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create) · [查询接口](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-query)
- **单条下载绕过 FastAPI 数据流属已有能力。** 对象缺失在单条下载入口会被拦截；批量 ZIP 仍由 API 流式传输。CDN 地址替换后能否通过 SigV4 校验取决于实际回源 Host 配置，本轮未验证部署。[下载](../../backend/app/api/works.py#L225) · [CDN 签名](../../backend/app/core/storage.py#L120)
- **JWT 不适用。** 当前身份凭证是不同角色的 Redis SID；本轮未看到 JWT 路径。[会话实现](../../backend/app/core/sessions.py#L32)

## 需要后续定范围的发现

| 发现 | 条件和影响 | 处理归属 |
|---|---|---|
| 考生删除会破坏运行任务关联 | 物理删除 Candidate 和 Quota 后，Worker 内连接无法加载 Work，已加载任务若失败也可能因配额缺失回滚；按人查询会失去关联。 | 007 设计评审明确归档或快照；001 核对退款凭据。 [删除](../../backend/app/admin/candidates.py#L685) · [读取](../../backend/worker/state.py#L80) · [退款](../../backend/app/services/handoff_sync.py#L231) |
| 管理员调额存在检查与更新间隙 | 管理员读到旧 used 后考生扣额，随后调额可使 total 小于 used 而绕过确认门槛。基于源码确认竞态窗口，本轮无并发复现。 | 001 评审列为独立验收或审批后的子任务。 [调额](../../backend/app/admin/quotas.py#L114) |
| 前端会丢失跟踪或创建另一 Work | generate 已提交但响应丢失时，再点会先创建新 Work；状态请求失败会停轮询，刷新只恢复一条。 | 保留在后续前端事项，当前十单不夹带页面重构。 [工作台](../../frontend/src/views/candidate/WorkspaceView.vue#L406) |
| 审计来源依赖转发头边界 | 代码读取 `X-Forwarded-For` 首项，现有 Nginx 追加链条；可信边界需统一，避免审计 IP 不准确。 | 003 确立边界，004 验证。 [客户端地址](../../backend/app/core/deps.py#L18) · [网关](../../nginx/nginx.conf#L56) |

完整的执行状态见[验收标准](acceptance-criteria.md)。这次只交付本地文档。
