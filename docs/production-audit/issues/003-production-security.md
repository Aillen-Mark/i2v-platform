# 003 生产启动校验与默认配置收口

**优先级** P0
**审查结论** 已确认，静态审查。考生和管理员已有分离的 Redis SID 会话，无待加固的 JWT 实现。
**发布状态** 待批准

## 问题背景

本机演示配置如果直接迁往公网，会带入调试用默认值和明文传输边界。

## 实际代码依据

`Settings` 含默认凭据及 Mock/调试配置；Fernet 自动回写 `.env`；Cookie 固定为非 Secure。审核将字段转换为 `bool`，未强制 JSON 布尔值。入队日志带完整签名链接。[配置](../../../backend/app/core/config.py#L34) · [Cookie](../../../backend/app/core/sessions.py#L132) · [审核](../../../backend/app/services/moderation.py#L35) · [日志](../../../backend/app/services/handoff.py#L62)

## 风险说明

错误配置可能让生产以默认密码、模拟成片或全放行审核启动。多进程生成不同 Fernet 密钥会破坏初始密码解密。签名 URL 泄漏可扩大临时对象访问范围。

## 改造目标

生产模式失败即退出，真实审核严格校验，生产 Cookie 启用 Secure，敏感日志去除链接及凭据。

## 涉及文件

`.env.example`、`backend/app/core/config.py`、`sessions.py`、`deps.py`、`backend/app/main.py`、`backend/app/services/moderation.py`、`handoff*.py`、`backend/worker/config.py` 及测试；Nginx 契约由 004 落地。

## 修改范围

显式生产模式与启动验证，拒绝默认/空凭据、Mock、无效 Fernet 和不可用的审核配置。确保公开 URL 不被记入业务日志。明确代理信任边界和应清洗的头，验证 JSON 布尔类型。

## 明确不修改的内容

不引入 JWT、不重做认证系统或账号数据、不覆盖服务器现有环境配置。配额、Outbox 与故障恢复在各自 Issue 实施。

## 数据库影响

无迁移。生产 Fernet 必须沿用能解密现有初始密码的密钥。已有管理员凭据更换须走授权运维流程。

## 兼容性要求

保留开发和测试环境的显式 Mock 模式，Cookie 名称、路由和认证返回字段保持兼容。生产模式与实际 HTTPS 边界配置相配。

## 测试要求

配置正反例、多进程读取相同预置密钥、Secure Cookie、审核畸形响应拒绝、日志脱敏。由 004 验证 TLS、转发头和公网端口。

## 验收标准

- [ ] 生产坏配置启动失败，退出信息可以定位配置项且不回显值。
- [ ] 生产 HTTPS 登录和下载正常，日志不含签名 URL、Token 或密码。
- [ ] 合法生产配置可启动，开发模式功能可回归。

## 回滚方案

回退应用版本前保持经批准的生产密钥和 TLS。不可用开发模式绕过生产门禁；失效的上线配置先恢复到上一份经过验证的配置。

## 依赖的其他 Issue

无。004 的网关与服务使用本单确立的配置契约。

## 实施计划

第一批及 003 已批准执行。GitHub 发布状态仍待批准。

| 文件 | 计划改动 |
|---|---|
| `.env.example` | 保留现有本地示例，注明生产环境标记、显式密钥、HTTPS 审核和代理部署要求。示例只使用占位值或现有开发值。 |
| `backend/app/core/config.py` | 在任何 Fernet 回写之前解析环境。生产模式拒绝 DEBUG、SQL_ECHO、MOCK_WORKER、非 HTTP 审核、无效或明文审核 URL，以及缺失或已知默认的管理员、业务 MySQL、Redis 和 MinIO 凭据。生产必须预置有效 Fernet，不自动生成、轮换或写文件。错误只输出配置项和安全原因。业务进程不要求注入 MySQL root 密码；数据库容器的 root 密码校验留给部署入口。 |
| `backend/app/main.py` | 复用统一配置门禁，确保在连接依赖或创建默认管理员前失败。保留现有 API 注册和响应契约。 |
| `backend/app/core/sessions.py`、`backend/app/core/deps.py` | 生产下会话 Cookie 写入和清除都带 Secure，保留 Cookie 名称、TTL、HttpOnly 和 SameSite。审计 IP 读取 ASGI 已解析的客户端地址。代理信任清单和转发头覆盖在 004 的网关部署配置落实，本单不改 Nginx。 |
| `backend/app/services/moderation.py` | HTTP 审核只接受符合现有约定的 JSON 布尔字段和可选字符串原因；超时、连接异常、HTTP 错误、畸形响应走拒绝。使用固定的用户侧错误文案，日志记录安全分类及状态码。 |
| `backend/app/services/handoff.py`、`handoff_sync.py` | 删除普通入队日志里的完整首帧签名 URL，保留 Work ID、引擎及画幅。维持现有 Celery 参数和回调签名，消息最小化交给 001。 |
| `backend/worker/config.py`、`backend/worker/tasks.py` | API 和 Worker 共享业务配置门禁。仅 Worker 在启动时校验它负责的 MiniMax Key 和 HTTPS 上游地址，不向 API 进程额外分发模型 Key。保持本地协议测试能力，失败日志不回显凭据。 |
| 新增 `backend/tests/test_production_security.py` | 覆盖合法及不合法配置、旧 Fernet 解密兼容、Cookie 属性、审计地址、审核超时与坏响应、交接日志脱敏。使用进程隔离或临时配置文件，不触碰现有 .env 和演示数据。 |

### 数据、接口与部署影响

- 无数据库结构调整、数据回填、前端变更或新依赖。沿用现有 Fernet 可继续解密已保存的初始考生密码。管理员已有密码和账户数据不在启动时修改。
- 请求和响应结构保持兼容。审核服务不可用时拒绝本次上传，沿用现有审核失败响应契约。生产 Cookie 需要通过 HTTPS 使用，本地 HTTP 仍沿用开发配置。
- 本单不改 Docker Compose 和 Nginx，也不连接生产主机。004 落地 TLS、代理地址信任、访问日志与端口；在隔离环境验证生产配置启动。临时不具备真实审核端点时，仅用本地协议夹具验证请求与拒绝逻辑，不记录为生产审核通过。

### 验证和回滚

先跑新增定向用例，再跑认证、上传、交接及 Worker 回归；隔离 MySQL、Redis、MinIO 可用时执行完整现有测试。记录命令及实际结果。检查 Git diff、Git status 和日志脱敏。没有数据库迁移，应用级回滚使用上一批准版本并沿用原密钥。生产若无合格配置则暂停入口，不能将 Cookie 安全或审核门禁切回开发配置。

## 实施记录

003 已完成本地实现。生产配置在连接依赖与 Fernet 回写前校验。自动生成 Fernet 仅用于开发，生产必须预置原密钥。Cookie 的 set/delete 使用相同 Secure 策略，审计 IP 取 ASGI 已解析地址。审核非布尔、错误响应和连接错误统一拒绝。同步和异步派发日志不记录首帧签名 URL。Worker 保留启动时的 Key 校验并拒绝生产上游明文地址。业务数据库 DSN 和 Redis URL 编码凭据里的保留字符。

- 定向运行 `python -m pytest -p no:cacheprovider tests/test_production_security.py -q`，9 项通过。
- 回归运行 `python -m pytest -p no:cacheprovider tests worker/test_worker.py -q`，53 项通过。使用本机真实 MySQL、Redis 和独立测试桶，S3 由 Moto 提供；审核协议使用本地 HTTP 服务，未请求真实审核厂商或 MiniMax。
- 相关 Python 文件的 Ruff 检查和 Git diff 检查通过。
- Ubuntu 24.04 上的 Nginx TLS、可信代理和实际审核服务另由 004 验证。现有生产环境和数据未修改。
