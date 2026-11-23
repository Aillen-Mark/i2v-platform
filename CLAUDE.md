# CLAUDE.md · 图生视频创作平台（执行端规约）

> 每次开始工作前先读本文件，再读 `docs/progress.md` 了解当前进度。
> 事实来源优先级：`docs/requirements.md` > `docs/handoff-contract.md` > `docs/decisions.md` > `docs/schema.sql` > 你的推断。

## 1. 项目与角色

- 项目：面向考试场景的图生视频创作平台，单批 100 人并发。考生登录 → 选题 → 传图写提示词 → 生成 15 秒视频 → 下载；管理员管理考生、题目、配额、时段、模型、用量。
- 你是**全栈执行端**，负责本仓库除「视频生成 Worker」以外的全部代码。

### 你负责
工程脚手架与 Docker Compose、8 张表的 ORM、Redis 会话与队列状态读取、考生/管理员双端隔离认证、Excel 导入与密码条、题目/配额/时段管理、创作提交网关（原子扣减配额）、内容审核接入层、对象存储签名下载与流式打包、模型配置后台、5 维度统计、操作日志、Vue 3 双端前端。

### 你不负责（同伴负责，严禁实现）
真实的 MiniMax 调用、Celery 消费逻辑、并发信号量、超时/重试/熔断、僵尸任务对账、ffmpeg 转码。
与生成模块的**唯一交汇点**是 `backend/app/services/handoff.py` 与 `handoff_sync.py`，接口必须严格遵守 `docs/handoff-contract.md`。本地开发默认 `MOCK_WORKER=true`，由 Mock 模拟出片/失败。

## 2. 技术栈（硬约束，不要擅自替换）

- 后端：Python 3.11+、FastAPI、SQLAlchemy 2.0（FastAPI 侧纯异步 `AsyncSession` + `aiomysql`）、`redis.asyncio`、Pydantic v2、`openpyxl`、`passlib[bcrypt]`、`cryptography`（Fernet）、`aioboto3`/`boto3`（S3 兼容）、`zipstream-ng`、pytest + httpx。
- 前端：Vue 3（Composition API + `<script setup>`）、Vite、TypeScript、Pinia、Vue Router、Element Plus。
- 基础设施：Docker Compose（MySQL 8.0、Redis 7、MinIO 作为本地 S3、Nginx）。云厂商未定，存储层只写 S3 兼容接口，通过 `.env` 切换 endpoint。
- 数据库结构以 `docs/schema.sql` 为准；如需改表，同步修改 `schema.sql` 并在 `docs/decisions.md` 记录原因。

## 3. 目录约定

```
backend/app/core/        config, database, redis, security, storage, deps
backend/app/models/      ORM 模型（与 schema.sql 一一对应）
backend/app/schemas/     Pydantic 模型
backend/app/api/         考生端路由（/api/*）
backend/app/admin/       管理端路由（/admin/*）
backend/app/services/    业务服务：quota, time_window, works, audit, moderation, handoff, handoff_sync
backend/tests/           pytest，连接 docker 里的真实 MySQL/Redis（不要用 SQLite 代替）
frontend/                Vue 3 工程，考生端与管理端两套布局
```

## 4. 核心铁律（违反即视为未完成）

1. **不写占位**：禁止 `TODO`、`pass`、`...`、"此处省略"。当前阶段范围内的接口必须完整可运行。
2. **规则在服务端**：配额、时段、账号状态全部由后端判定，前端只做展示。
3. **配额原子扣减**：只能用单条
   `UPDATE quotas SET quota_used = quota_used + 1 WHERE candidate_id=:cid AND question_id=:qid AND quota_used < quota_total`，并校验 `rowcount == 1`。
4. **提交生成必须在一个事务里完成**（顺序固定）：
   1. 校验考生 `status=1`
   2. 校验当前处于任一启用时段
   3. `SELECT ... FOR UPDATE` 锁定该 work（且属于本人）；若状态不是 `draft`，直接返回当前状态（幂等，不再扣次数）
   4. 原子扣减配额，`rowcount == 0` 则回滚并返回「本题机会已用完」
   5. 计算 `seq`，更新 work 为 `queued`，写 `engine_code`、`submitted_at`
   6. 提交事务后才 `ZADD` 排队队列并调用 `trigger_video_generation`；**若入队抛异常，立即调用 `mark_work_failed_and_refund(error_code="ENQUEUE_FAILED")`**，不能吞次数。
5. **失败退还幂等**：`SELECT ... FOR UPDATE` 锁 works 行，`quota_refunded == 1` 或已 `succeeded` 时直接返回；退还用 `GREATEST(quota_used - 1, 0)`；同事务写 `audit_logs`。
6. **会话隔离**：考生 Cookie `cand_sid`、管理员 Cookie `admin_sid`，`HttpOnly`、`SameSite=Lax`、`Max-Age=86400`；Redis 单点会话，同账号后登录踢掉前一个。
7. **密码**：`password_hash` 用 bcrypt；初始密码另存 Fernet 加密的 `init_password_enc`，`init_pwd_expire_at` 过后不可查看、只能重置。日志、异常、响应中不得出现明文密码（导出与密码条接口除外）。
8. **Mock 协程**：`asyncio.create_task` 创建的任务必须保存引用（如模块级 set），防止被 GC。
9. **审计**：导入考生、重置密码、调整配额、增删题目、修改/延长时段、启停模型、失败退还，全部写 `audit_logs`（含 before/after）。
10. **时间**：数据库存 UTC，接口返回 ISO 8601 带时区；前端按 `Asia/Shanghai` 展示。

## 5. 工作方式

- **一次只做一个阶段**。阶段指令在 `prompts/` 下，由用户逐个下发；不要提前实现后续阶段的内容。
- 开工前先列出将要创建/修改的文件清单和实现要点，然后直接写代码，不要停在方案。
- 需求有歧义时：按 `docs/decisions.md` 已有决定执行；没有决定的，选最保守、最符合需求文档原文的做法，把假设追加到 `docs/decisions.md` 的「执行端假设」一节，并在汇报中列出。
- 每个阶段结束前必须：
  1. 运行 `docker compose up -d` 后执行该阶段的 pytest，全部通过；
  2. 更新 `docs/progress.md`（完成项、未完成项、已知问题、如何启动与测试）；
  3. 用简短清单汇报：做了什么、测试结果（贴出 pytest 摘要）、假设与风险、建议用户手动验收的步骤。
- 不要修改 `docs/requirements.md` 与 `docs/handoff-contract.md`；如认为契约有问题，在汇报中提出。

## 6. 常用命令（阶段 0 建好后保持可用）

```bash
cp .env.example .env
docker compose up -d mysql redis minio
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload          # http://localhost:8000/docs
pytest -q
cd frontend && npm i && npm run dev
```
