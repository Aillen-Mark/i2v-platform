请先完整阅读 `CLAUDE.md`、`docs/requirements.md`、`docs/schema.sql`、`docs/handoff-contract.md`、`docs/decisions.md`、`docs/progress.md`。

现在开始 **Phase 1：工程底座 + 里程碑 D1（认证、考生管理、题目管理）**。只做本阶段，不要提前实现配额调整、时段、作品提交、下载、统计和前端。

## 一、工程底座

1. 根目录 `docker-compose.yml`：`mysql:8.0`（挂载 `docs/schema.sql` 到 `/docker-entrypoint-initdb.d/` 自动建表，带 healthcheck）、`redis:7-alpine`、`minio`（启动时自动创建存储桶）。`backend` 服务可以先写好但允许本地 uvicorn 直接跑。
2. `.env.example`：数据库、Redis、MinIO/S3、`AES_FERNET_KEY`、`MOCK_WORKER=true`、`MODERATION_PROVIDER=mock`、`INIT_PWD_VIEW_HOURS=72`、`ADMIN_INIT_USERNAME`、`ADMIN_INIT_PASSWORD`。附一行生成 Fernet key 的命令。
3. `backend/requirements.txt` 与 `app/core/`：`config.py`（pydantic-settings）、`database.py`（async 引擎 + 供 `handoff_sync.py` 使用的 sync 引擎）、`redis.py`、`security.py`、`storage.py`（S3 兼容：上传、预签名读链接）、`deps.py`（考生/管理员当前用户依赖）。
4. `app/models/`：与 `schema.sql` 一一对应的 8 张表 ORM。
5. `app/services/handoff.py` 与 `handoff_sync.py`：按 `docs/handoff-contract.md` 实现契约 A/B/C。`MOCK_WORKER=true` 时 Mock 流程：2 秒后转 running → 再 4 秒按 80% 成功 / 20% 失败调用契约 B 或 C（概率可通过环境变量设为 0 或 1，方便测试）。
6. `app/main.py`：路由注册、统一错误响应格式 `{code, message, detail}`、`/healthz`、启动时创建默认管理员。

## 二、D1 接口

1. **认证**
   - 考生：`POST /api/login`、`POST /api/logout`、`GET /api/me`（本阶段返回基本信息即可）。
   - 管理员：`POST /admin/auth/login`、`POST /admin/auth/logout`、`GET /admin/auth/me`。
   - 连续输错 5 次锁定 5 分钟；记录 `last_login_at`、`last_login_ip`；禁用账号不能登录；同账号后登录踢掉前一个；两端 Cookie 与会话完全隔离。
2. **考生管理 `/admin/candidates/*`**
   - Excel 模板下载（列：姓名、账号〔可空，按规则自动生成〕、备注）。
   - Excel 导入：批量查重 + 批量插入，1000 行 ≤10 秒；空姓名、重复账号（库内重复与表内重复）按行号返回错误，不中断有效行；返回成功数、失败数、错误明细；为每个新考生补齐所有启用题目的配额行；写 `audit_logs`。
   - 列表（分页、按账号/姓名搜索）、新增、编辑、删除单个考生。
   - 导出 Excel/CSV（账号、姓名、初始密码；过期显示「已过期，请重置」）；密码条数据接口（供前端打印）。
   - 单个/批量重置密码、批量启禁用，均写审计日志。
3. **题目管理**
   - `/admin/questions/*`：增删改查（软删除）、排序、参考图上传、每题 3 条示例提示词、默认次数。
   - 新增题目时**同一事务内**用 `INSERT IGNORE INTO quotas ... SELECT ...` 为全体考生补建配额行。
   - `GET /api/questions`：返回启用题目及每题「剩余 N / 共 M 次」、是否已调整。

## 三、测试与交付

编写 `backend/tests/test_d1.py`（连 docker 里的 MySQL/Redis，每个测试前清理数据），至少覆盖：
- Excel 导入 100 人成功；含空姓名、重复账号的文件按行号报错且有效行正常导入；1000 行导入耗时 ≤10 秒
- 生成的密码 8 位、含大小写和数字、不含 O/0/I/l/1
- 输错 5 次锁定；同账号二次登录后旧 Cookie 失效；考生 Cookie 访问管理端接口返回 401
- 新增题目后全体考生自动有配额行；3 道题时考生总次数为 6
- `handoff` 契约 C 重复调用只退还一次

全部通过后，按 `CLAUDE.md` 第 5 节更新 `docs/progress.md` 并汇报。
