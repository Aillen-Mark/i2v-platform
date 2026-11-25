# 决策与待确认

## 已定决策

| # | 事项 | 决定 |
|---|---|---|
| 1 | 模型供应商 | 只用 MiniMax：wq-v1（768P 主用）、wq-v1-lite（480P 降级）。真实模型名与 15 秒支持情况以实测为准 |
| 2 | 初始密码可查看 vs bcrypt | 登录用 bcrypt；另存 Fernet 加密的初始密码，生成后 `INIT_PWD_VIEW_HOURS`（默认 72）小时内可查看，过期只能重置 |
| 3 | 提交校验顺序 | 账号状态 → 时段 → 幂等（锁 work 并检查 draft）→ 原子扣减配额 → 入队（先幂等再扣配额，避免重复扣） |
| 4 | 入队失败 | 立即调用失败退还，error_code=ENQUEUE_FAILED |
| 5 | 对象存储 | 云厂商未定；代码只写 S3 兼容接口，本地用 MinIO |
| 6 | 内容审核 | 抽象为 `services/moderation.py` 接口，默认 `MODERATION_PROVIDER=mock`（全部通过），后续接云厂商 |
| 7 | 示例提示词 | 每道题单独配置 3 条（`questions.prompt_examples`），为空时用全局默认 3 条 |
| 8 | 删除题目 | 软删除（status=0）：考生端不再展示、不可提交；已有作品与配额保留，可下载可统计 |
| 9 | 草稿清理 | 超过 24 小时未提交的 draft 作品由定时任务清理（删记录与图片） |
| 10 | 打包下载 | 流式 zip（zipstream-ng）边拉边传，不落本地磁盘 |
| 11 | 默认管理员 | 首次启动若 admins 为空，用 `.env` 的 `ADMIN_INIT_USERNAME/ADMIN_INIT_PASSWORD` 创建 |

## 待需求方确认（确认后更新上表）

1. 题目内容：统一命题还是各不相同？是否需要参考图？
2. 输出规格：下游对分辨率、码率、大小、是否带音频有无硬要求？
3. 首次开放的日期与时段，由谁确认？
4. 初始密码统一还是每人随机？密码条由谁分发？
5. MiniMax 是否企业主体开通？能否提额到 50+ 并发？
6. 内容审核用哪家云厂商？

## 执行端假设（Claude 在开发中追加）

| # | 日期 | 假设 / 变更 | 原因 |
|---|---|---|---|
| A1 | 2026-11-23 | MySQL 端口 13306、Redis 端口 16379（MinIO 保持 9000/9001） | 本机 Windows 原生 MySQL(3306) 与 Redis(6379) 服务已在运行，容器端口按手册「占用即顺延」规则改为 13306/16379，并同步写入 `.env` |
| A2 | 2026-11-23 | 初始管理员密码同时支持 `INIT_ADMIN_PASSWORD` 与 `ADMIN_INIT_PASSWORD` 两个键 | 手册用 `INIT_ADMIN_PASSWORD`，本文件第 11 条用 `ADMIN_INIT_PASSWORD`；实现取前者，为空时回落后者 |
| A3 | 2026-11-23 | `audit_logs.target_id` 对失败退还写 `works.task_no` | 手册测试要求「按 task_no 统计退还记录」；task_no 是对外唯一标识，便于与对账脚本关联 |
| A4 | 2026-11-23 | Mock Worker 时序参数化：`MOCK_QUEUE_DELAY` / `MOCK_RUN_DELAY`（默认各 1 秒）、`MOCK_SUCCESS_RATE`（默认 0.8） | 手册要求「短暂等待」，同时需要测试可确定性地跑成功/失败分支 |
| A5 | 2026-11-23 | 数据库时间统一存 UTC（naive DATETIME），MySQL 容器 `--default-time-zone=+00:00` | 遵循 CLAUDE.md 铁律 10 |
| A6 | 2026-11-23 | Docker Desktop 安装目录 `E:\Docker\DockerDesktop`、WSL 数据根 `E:\Docker\wsl` | 用户要求数据全部落在 E 盘，不占用 C 盘空间 |
| A7 | 2026-11-23 | bcrypt cost 默认 10，可由 `BCRYPT_ROUNDS` 配置 | 实测：cost=12 时 1000 个初始密码 16 线程需 19.3 秒，无法满足「1000 行 ≤10 秒」；cost=10 实测整批导入 5.3 秒，单次校验约 56ms 仍满足「登录 ≤800ms」。cost 写在哈希里，改配置不影响存量密码 |
| A8 | 2026-11-23 | 账号留空时自动生成规则：`stu` + 4 位递增序号（`AUTO_ACCOUNT_PREFIX`，从 1 起、跳过已占用） | 需求 4.1 只写「系统按规则自动生成」，未指定具体规则 |
| A9 | 2026-11-23 | 管理端登录保护用 Redis 计数器（连续 5 次失败锁 5 分钟），与考生端 DB 字段方案并存 | `admins` 表按 schema.sql 只有 last_login_at，无 failed_login_count / locked_until，不擅改表结构 |
| A10 | 2026-11-23 | 删除考生为硬删除，同事务删除其 quotas 行；works 记录保留 | 需求要求「删除单个考生」但未说明历史作品归属；保留 works 才能保证已出片作品可下载、统计可对账 |
| A11 | 2026-11-23 | 会话键：`i2v:sess:{role}:{sid}`（会话体）+ `i2v:sess:{role}:cur:{user_id}`（当前 sid 指针） | 契约 §6 只约定队列与引擎键；「同账号后登录踢掉前一个」需要指针键定位旧会话 |
| A12 | 2026-11-23 | 软删除题目（status=0）不删 quotas；考生端「总次数」只统计启用题目 | 决策 8 要求配额保留可下载可统计；总次数口径与需求 4.3「各启用题目的次数之和」一致 |
| A13 | 2026-11-23 | 本机环境 `NO_PROXY` 含 `[::1]`，httpx 默认 `trust_env=True` 解析代理会抛 `InvalidURL`；测试与本地脚本访问 MinIO 一律 `trust_env=False` | 环境变量问题，非应用缺陷；云端部署时 NO_PROXY 不含该写法即不受影响 |
| A14 | 2026-11-23 | Excel 导入单次上限 5000 行（`EXCEL_IMPORT_MAX_ROWS`），文件上限 20MB | 需求只要求 1000 行 ≤10 秒；设上限避免超大文件拖垮单请求 |
| A15 | 2026-11-23 | **M2**：会话键改为 `i2v:session:{role}:{sid}`（值为 user_id）+ `i2v:active_sid:{role}:{user_id}`，sid 为 32 字节 hex（`secrets.token_hex(32)`） | M2 手册 Step 1 明确键名与 sid 形态，替代 A11 |
| A16 | 2026-11-23 | **M2**：登录锁定统一返回 **429**（原 423），错误码仍为 `ACCOUNT_LOCKED`，`detail.retry_after` 给剩余秒数 | M2 手册 §Step 1 要求 429；`tests/test_d1.py` 的对应断言已同步改 |
| A17 | 2026-11-23 | **M2**：自动考号规则改为 `C2026` + 6 位递增序号（`AUTO_ACCOUNT_PREFIX` / `AUTO_ACCOUNT_DIGITS`），替代 A8 的 `stu0001` | M2 手册 §Step 2 给出的示例规则 |
| A18 | 2026-11-23 | **M2**：`DELETE /admin/questions/{id}` 默认物理删除题目并清理其配额行；若 `works` 已有该题作品则返回 409 并提示改用停用；`?soft=true` 时走停用（status=0，保留配额） | M2 手册 §Step 3 的删除保护要求；与决策 8 / A12 的软删除并存，软删除改为显式参数 |
| A19 | 2026-11-23 | **M2**：初始密码可查看期 168 小时（7 天），`INIT_PWD_VIEW_HOURS=168` | M2 手册要求导入/重置后 `init_pwd_expire_at = now + 7 天` |
| A20 | 2026-11-23 | **M2**：批量哈希走 `ProcessPoolExecutor`（spawn，`HASH_POOL_WORKERS=14`），应用启动时预热；实测本机（R8-5800H，8C/16T）1000 个 bcrypt(cost=10) 约 6.3 秒，整批导入 5.1~7.6 秒 | 手册 §3.1 要求进程池；14 worker 实测优于 16/24/32（SMT 争用）。进程池异常时自动降级线程池，绝不降 cost |
| A21 | 2026-11-23 | **M2**：导出列扩为 `账号 / 姓名 / 初始密码 / 状态 / 备注`，并支持 `ids` 勾选导出 | M2 手册 §Step 2.8 |
| A22 | 2026-11-23 | **M2**：`GET /api/me` 增加 `total_used`、`in_window`、`next_window_start`、`current_window_end`；`GET /admin/candidates/{id}/init-password` 过期改为 **410 Gone**；`GET /admin/questions` 默认含停用题目 | M2 手册 §Step 1.2 / §Step 2.5 / §Step 3.1 |
| A23 | 2026-11-23 | **M3**：提交网关落在 `services/works.py`，严格按 M3 手册 5 步链：归属+幂等（`FOR UPDATE` 锁 work）→ 账号/题目状态 → 时段强校验 → **单条原子 UPDATE 扣配额（rowcount==1）** → 序号+引擎+queued+commit → 事务外入队（异常即 `mark_work_failed_and_refund`） | M3 手册 §Step 3.2 与验收标准 #6 |
| A24 | 2026-11-23 | **M3**：默认引擎解析 `services/engine.py`，优先 Redis Hash `i2v:engine:default`（TTL 600s），未命中回源 `engines` 表并回填；字段沿用交接契约 §6（model/resolution/duration/max_concurrency） | 契约 §6 的键位约定，避免每次提交都查库 |
| A25 | 2026-11-23 | **M3**：排队位次取 `ZRANK i2v:queue:waiting_ids`（0 基），预计等待 `= int(((ahead+1)/max(max_concurrency,1)) × avg_cost_sec)`，`avg_cost_sec` 取 `i2v:stats:avg_cost_sec` 默认 120 | M3 手册 §Step 3.3 公式原文 |
| A26 | 2026-11-23 | **M3**：配额下调保护统一返回 **409 `QUOTA_DOWNGRADE_CONFIRM_REQUIRED`**，`detail` 带 `affected_count` 与最多 20 条 `samples`；`force_confirm=true` 才执行，且全部置 `adjusted_flag=1` | 验收标准 #5 |
| A27 | 2026-11-23 | **M3**：时段延长必须 `confirm=true`（否则 400 `CONFIRM_REQUIRED`），新结束时间必须晚于原结束时间（否则 400 `INVALID_EXTEND`）；`new_end_at` 与 `extend_minutes` 二选一 | M3 手册 §Step 1.4 |
| A28 | 2026-11-23 | **M3**：异步连接池调为 `pool_size=20, max_overflow=30` | 手册 §3.3：支撑 20+ 并发抢配额压测，避免 QueuePool overflow |
| A29 | 2026-11-23 | **M3**：测试收尾先 `await handoff.wait_mock_tasks()` 再 TRUNCATE | TRUNCATE 会重置自增 ID，残留 Mock 协程可能误标新测试里同 ID 的作品 |
| A30 | 2026-11-23 | **M4**：成片下载签名有效期严格 **1800 秒**，通过 `ResponseContentDisposition` 强制另存为 `{账号}_{题号}_第{seq}次_{时间戳}.mp4`；中文用 RFC 5987 `filename*=UTF-8''...` + ASCII 回退名；文件名时间戳按东八区展示 | M4 手册 §Step 1.2 与验收标准 #10 |
| A31 | 2026-11-23 | **M4**：`/api/me/download-all` 用 `zipstream-ng`（`compress_type=0` 即 ZIP_STORED）边读 MinIO 边吐流，同名冲突追加 `(2)(3)`；无已出片作品返回 400 | 手册 §3.1 防内存溢出；MP4 已编码不重复压缩 |
| A32 | 2026-11-23 | **M4**：新增 `CDN_BASE_URL` 配置，非空时把签名 URL 的 scheme+netloc 换成 CDN 域名（签名参数不动） | 手册 §3.3 跨端口/跨域名可访问性 |
| A33 | 2026-11-23 | **M4**：引擎健康度优先读 Redis `i2v:engine:health:{code}`，缺失时用 `works` 最近 1 小时兜底统计；失败率 >20% 或 `circuit_open=1` 判 `is_healthy=false` | 手册 §Step 2.1 |
| A34 | 2026-11-23 | **M4**：当前默认引擎不允许直接停用（400 `ENGINE_DEFAULT_CANNOT_DISABLE`），必须先切换默认；切默认时目标引擎自动 `status=1` | 手册 §Step 2.2 |
| A35 | 2026-11-23 | **M4** 统计口径：`works_total` 不含 draft；成功率 = succeeded/works_total；累计时长与费用只算 succeeded，费用 = `SUM(duration_sec × price_per_sec)`（按 `works.engine_code` 关联 engines）；`by-engine` 调用数含失败、时长只算成功、占比按调用条数 | 手册验收标准 #11「逐笔对账」 |
| A36 | 2026-11-23 | **M4**：管理端批量成片只导出**下载清单**（CSV/JSON，规范文件名 + 30 分钟签名直链），不做实时打包；单考生维度才走流式 zip | 手册 §3.2：几百个视频近 10GB，实时打包必卡死 |
| A37 | 2026-11-23 | **M5**：Mock 出片改为**真出片**——成功分支把 `backend/app/assets/mock_sample.mp4`（1.1MB，H.264+AAC，moov 前置）真实上传到 `i2v-videos` 的 `videos/mock/{task_no}.mp4`，再回调契约 B；`handoff_sync` 同步对齐 | 前端要能真实 `<video>` 播放与下载，占位 key 会 404 |
| A38 | 2026-11-23 | **M5**：前端 Vue3 + Vite5 + TS + Pinia + Vue Router + Element Plus + Axios，dev server 固定 `127.0.0.1:5173`，`/api`、`/admin`、`/healthz` 代理到 `127.0.0.1:8000`（`changeOrigin`） | 同源才能让 HttpOnly Cookie `cand_sid` 正常携带，彻底规避跨域丢 Cookie |
| A39 | 2026-11-23 | **M5**：图片上传前用 `<canvas>` 等比缩放，长边 >1920 才压缩（`image/jpeg` q=0.9），长边本来就 ≤1920 的直接用原文件；压缩结果与原始尺寸都展示给考生 | 需求 3.1/4.5 要求前端压到长边 1920，避免 4000px 大图拖慢上传与上游 |
| A40 | 2026-11-23 | **M5**：本地草稿按 `i2v:draft:{candidateId}:{questionId}` 存 localStorage，图片以 DataURL 暂存；命中 5MB 上限（QuotaExceededError）时降级为只存提示词并提示；提交成功后清除该题草稿 | 需求 4.5 刷新/断线恢复；必须处理容量上限，不能静默丢草稿 |
| A41 | 2026-11-23 | **M6**：pytest 物理隔离——`conftest.py` 在导入任何 app 模块前改写 `MYSQL_DATABASE=i2v_platform_test`、`REDIS_DB=15`；启动时用 root 建库 + 授权 + 同步 `docs/schema.sql`（1050/1062 幂等跳过） | 红线：pytest 绝不能清主库 `i2v_platform` 与 db=0 |
| A42 | 2026-11-23 | **M6**：管理端前端页面统一走 `/console/*`，后端管理接口保持 `/admin/*` | 页面路由与 API 路径同形会导致「浏览器刷新 /admin/xxx 拿到 JSON 401」，用前缀物理隔离最稳 |
| A43 | 2026-11-23 | **M6**：Nginx 网关 `127.0.0.1:8080`，托管 `frontend/dist`（`try_files` 回退 SPA），`/api/`、`/admin/`、`/healthz` 反代 `host.docker.internal:8000`；`client_max_body_size 15m`；`limit_req` 20 r/s（burst 40，按客户端 IP） | 生产入口需要静态托管 + 同源 Cookie + 上传体积 + 基础限流 |
| A44 | 2026-11-23 | **M6**：压测客户端用 4 进程 × 25 并发，而不是单进程 100 协程 | 实测同机单进程 100 并发 P95≈3.6s，4 进程×25 时 P95≈0.7s：单进程 asyncio 自身是瓶颈，真实考场是 100 个浏览器进程 |
| A45 | 2026-11-23 | **M6**：MySQL 加 `--innodb-flush-log-at-trx-commit=2` | 提交是「每请求一次 commit」的小事务，100 并发下 redo fsync 成为瓶颈；代价是极端断电可能丢最近 1 秒已提交事务（考试场景可接受，且已有重试/退还兜底） |
| A46 | 2026-11-23 | **M6**：boto3 客户端做成进程级单例（`max_pool_connections=50`）；下载 `file_size` 优先取 `works.file_size`，缺失才回源 S3 HEAD | 每次调用新建 boto3 客户端要 50~150ms，直接把下载 P95 拉到秒级 |
| A47 | 2026-11-23 | **M6.1**：Nginx 改为完整主配置（`worker_processes 1` + `upstream keepalive 256`），限流 `rate=100r/s burst=300 nodelay`；uvicorn 默认线程池放大到 32；登录会话写入改 Redis pipeline；压测客户端启动前预热 130 次网关请求 | 实测：多 worker 的 nginx 连接池被打散 + Windows 丢 SYN → 洪峰时出现 502；改单 worker + keepalive 后 502/503 归零，提交 P95 从 822ms 降到 580ms |
| A48 | 2026-11-24 | **M6.2**：单题极简模式——`SINGLE_QUESTION_MODE=True` 时启动兜底插入默认题 `Q1 / 图生视频创作`（无启用题目才插，含 3 条运镜示例与配额补建）；pytest 在 `conftest` 里置 `false`，保证题目数量断言不受影响 | 全场固定 1 道创作任务，但底层 `questions` / `quotas.question_id` / `works.question_id` 全部保留，恢复多题模式只需把开关改回 false |
| A49 | 2026-11-24 | **M6.2**：管理端屏蔽题目管理（`AdminLayout` 菜单注释 + `/console/questions` 走 `beforeEnter` 重定向，`QuestionsView.vue` 源码与 import 保留）；配额页锁定唯一启用题、按考生查配额只拉该题行；统计去掉「按题目」Tab | 恢复多题模式 = 删一行 `beforeEnter` + 放开一行菜单 |
| A50 | 2026-11-24 | **M6.2**：考生端工作台改居中单题布局，画幅不再手选，改为按首帧宽高比用 log 距离就近匹配后端允许的 5 种（16:9 / 4:3 / 1:1 / 3:4 / 9:16），偏差 >15% 提示会裁切或补边 | 后端 `ALLOWED_ASPECT_RATIOS` 只有这 5 个值，自动匹配只能产出这 5 个，21:9 仍被 400 拒绝 |
| A51 | 2026-11-25 | **M6.3**：全站视觉换装世赛中文官网语言（`#062B56` 深海蓝 / `#0085A1` 孔雀青 / `#F5A623` 琥珀黄、4~6px 微圆角、`0 8px 24px rgba(6,43,86,.07)` 克制阴影），`main.css` 同步覆盖 Element Plus 主题变量 | 摒弃后台白灰感与圆润互联网风；色板固定，禁止刺眼品红/紫 |
| A52 | 2026-11-25 | **M6.3**：新增原创 `GeoCubeDecor.vue`（等轴测立体方块簇，内联 SVG 三变体 hero/footer/login），双层深蓝顶栏 + Hero 巨幕 + 深蓝页脚；工作台加 X 型斜切几何背景与 `AI VIDEO STUDIO` 极淡巨幅水印 | 版权红线：禁用官方五指会徽、政府署名、实景摄影；所有几何图形与放射网格自绘，离线自包含（无外链字体/图片） |
| A53 | 2026-11-25 | **M6.3**：默认单题标题统一为「图生视频创意作品制作」；按手册要求恢复 3 枚浅青快捷示例词标签 | 与上一轮「去掉示例」冲突，按最新执行手册恢复；需要无示例时删 `.examples` 模板块与 `applyExample()` 即可 |
| A54 | 2026-11-25 | **M6.3.1 封版**：彻底移除工作台示例词（纯考试模式）；移除 `CandidateCreateIn` 的自定义 `password` 旁路，4 个密码产生点（Excel 导入 / 单条新增 / 单人重置 / 批量重置）唯一走 `generate_exam_password()`；新增单条建号密码合规单测（39 passed） | 密码零例外红线：杜绝任何自定义弱密码通道；`e2e_smoke.py` 增加返回密码合规断言，任何回归会让冒烟直接失败 |

