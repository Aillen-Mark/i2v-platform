# 图生视频创作考试平台 · 项目总交接与同伴联调文档 (HANDOFF.md)

> **文档版本**：v1.0（M1～M6.1 全栈底座封版）
> **更新时间**：2026-11-23
> **当前状态**：业务底座、前后端双端、Nginx 网关、38 项集成测试及 100 人并发压测已 **100% 封版通过**（当前运行在 `MOCK_WORKER=true` 本地真 MP4 模拟模式）。**唯一待办：同伴接入真实 Celery + MiniMax 视频生成 Worker。**

---

## 1. 我们在做什么任务 & 整体进度

### 1.1 项目定位

一套面向 **单批 100 名考生并发** 的在线图生视频考试平台（需求版本 v5.2）：

- **考生端**：登录 → 查看题目与剩余配额 → 上传首帧图（前端自动长边压至 ≤1920px）+ 填写提示词（本地草稿防丢）→ 扣减配额进入异步排队 → 3 秒轮询排队位次与进度 → 在线预览 MP4 与断点续传下载 / 打包下载。
- **管理端**：考生 Excel 1000 行极速导入、可打印 A4 密码条、题目管理（自动补齐配额）、配额风控（按题/按人/多人调整 + 下调拦截）、开放时段控制、模型引擎动态配置（含一键切 480P 降级开关）、5 维用量统计导出、操作审计日志。

### 1.2 里程碑完成清单（M1～M6.1 已全部绿灯封版）

| 阶段 | 模块内容 | 验收结果与关键指标 |
| :--- | :--- | :--- |
| **M1** | Docker 基建 (MySQL 8.0 + Redis 7 + MinIO) + 8 张核心表 + `handoff` 交接桩 | 容器全 `healthy`；5 并发退配额行锁幂等验证通过 |
| **M2** | 双端隔离登录（同账号互踢 + 5 次锁号）+ Excel 导人 + 题目联动补配额 | **1,005 行 Excel < 10s 导完**；新增题目自动补齐全体配额 |
| **M3** | 开放时段拦截 + 配额调整防下调冲突 (`409`) + 提交网关原子防超发 | 并发抢最后 1 次配额 **严格不超发**；时段外 100% 拦截 |
| **M4** | 对象存储 30 分钟预签名下载 (`206 Range`) + 流式 ZIP + 模型台 + 5 维统计 | 统计与明细逐笔对账一致；支持 BOM CSV 导出 |
| **M5** | 考生端 Vue3 闭环（Canvas 压图 + 草稿恢复 + 轮询排队 + 真 MP4 预览下载） | `vue-tsc` 零错误构建；端到端冒烟全绿 |
| **M6 & M6.1** | 管理端 Vue3 全套页面 (`/console/*`) + 测试库物理隔离 + Nginx 网关 + 百人压测 | **38 passed** 单测全绿；穿透 Nginx **100 并发提交 P95=580ms (0 个 5xx)**，**100 并发下载 206 首包 P95=540ms** |

---

## 2. 现场环境、目录结构与全部端口映射

### 2.1 核心路径（Windows 开发宿主机）

- **项目根目录**：`E:\DeepSeekHarness\frame\i2v-platform\i2v-platform`
- **Python 虚拟环境**：`backend\.venv\Scripts\python.exe`（Python 3.11.2）
- **Docker CLI 路径**：`E:\Docker\DockerDesktop\resources\bin\docker.exe`（数据盘全部落在 `E:\Docker\wsl`，不占 C 盘）
- **核心目录导航**：
  - `docs/schema.sql`：MySQL 8.0 八张表权威建表脚本
  - `nginx/nginx.conf`：生产级网关配置（静态资源 + API 反代 + `keepalive 256` + `100r/s burst=300` 限流）
  - `backend/app/services/handoff.py` & `handoff_sync.py`：**【你与同伴的唯一代码交接边界】**
  - `frontend/`：Vue 3 + Vite + TS + Pinia + Element Plus 双端工程（构建产物在 `frontend/dist`）
  - `scripts/`：`start_prod.ps1`（一键启动）、`stop_prod.ps1`（精准停机）、`seed_demo.py`（演示数据）、`load_test_100.py`（百人压测）、`e2e_smoke.py`（端到端冒烟）

### 2.2 端口与账号速查表（全部仅绑定 `127.0.0.1`）

| 服务 / 入口 | 访问地址 / 连接端口 | 默认账号 / 凭据（详见 `.env`） | 说明 |
| :--- | :--- | :--- | :--- |
| **考生端 Web（Nginx 生产入口）** | `http://127.0.0.1:8080` | `DEMO0001` / `6p7sq8ZY`（以 `seed_demo.py` 输出为准） | 正式访问与压测统一入口 |
| **管理后台 Web（Nginx 生产入口）** | `http://127.0.0.1:8080/console/login` | `admin` / `Admin@2026!` | 挂载在 `/console/*` 防刷新冲突 |
| **FastAPI 后端直连 / Swagger** | `http://127.0.0.1:8000/docs` | — | 生产模式默认 `--workers 4` |
| **MySQL 8.0（容器 `i2v-mysql`）** | `127.0.0.1:13306` → 容器 `3306` | 主库：`i2v_platform`（测试库：`i2v_platform_test`） | 避开了宿主机占用的 3306 |
| **Redis 7（容器 `i2v-redis`）** | `127.0.0.1:16379` → 容器 `6379` | 主库：`db=0`（测试隔离库：`db=15`） | 避开了宿主机占用的 6379 |
| **MinIO S3（容器 `i2v-minio`）** | API: `127.0.0.1:9000` / 控制台: `9001` | `minioadmin` / `minioadmin123` | 含公开桶 `i2v-public`、私有桶 `i2v-videos` |

### 2.3 日常一键启停与回归命令（PowerShell）

```powershell
cd E:\DeepSeekHarness\frame\i2v-platform\i2v-platform

# 1. 一键拉起全套生产服务（Docker 四容器 + Uvicorn 4 Workers）
.\scripts\start_prod.ps1

# 2. 精准停止后端与容器（只杀 8000 端口树，绝不误伤其他 Python 进程；加 -KeepDocker 可保留容器）
.\scripts\stop_prod.ps1 -KeepDocker

# 3. 跑全量 38 项自动化测试（物理隔离在 i2v_platform_test，绝不清主库数据）
.\backend\.venv\Scripts\pytest.exe -c backend\pytest.ini -v

# 4. 跑穿透 Nginx (8080) 的 100 人并发全链路压测
.\backend\.venv\Scripts\python.exe scripts\load_test_100.py

# 5. 前端构建（改完页面后重新发布给 Nginx 托管）
cd frontend ; npm run build
```

---

## 3. 给同伴的傻瓜式合流指南（接入真实 MiniMax Worker）

业务网关、配额扣减、排队前端展示、预签名下载已经全部替你铺好了。你只需要在 `backend/worker/`（或 `backend/app/worker/`）下写你的 Celery + MiniMax + `ffmpeg` 逻辑，按以下 **「改 1 个开关 + 接 3 个函数 + 守 4 个 Redis 键」** 即可无缝合流：

### 第一步：改 1 个环境变量开关

在根目录 `.env` 中，将默认开关从 `true` 改为 `false`：

```env
MOCK_WORKER=false
```

- 当 `MOCK_WORKER=true` 时：系统走内置协程模拟（自动把 `backend/app/assets/mock_sample.mp4` 真实上传到 MinIO 的 `videos/mock/{task_no}.mp4`，再回调成功/失败）。
- 当 `MOCK_WORKER=false` 时：`trigger_video_generation()` 在 `ZADD` 入队后，会**立即把任务派发给你的 Celery**——派发方式是读 `.env` 里的两个键：

  ```env
  CELERY_TASK_MODULE=worker.tasks          # 你的模块路径（按实际放法改，如 app.worker.tasks）
  CELERY_TASK_FUNC=enqueue_video_generation # 你的任务函数名
  ```

  代码等价于 `import_module(CELERY_TASK_MODULE)` → `getattr(module, CELERY_TASK_FUNC).delay(...)`。
  派发抛异常时，网关会**立刻调用 `mark_work_failed_and_refund(error_code="ENQUEUE_FAILED")` 退还次数**，不会吞考生机会。

### 第二步：对接仅有的 3 个交接函数（严禁直接裸改 `quotas` 表！）

> **贴心准备**：考虑到你的 Celery Worker 可能是**同步阻塞模式**运行，仓库里已经同时为你准备好了异步版 `app/services/handoff.py`（FastAPI 用）和**同步版 `app/services/handoff_sync.py`**（Celery 用），逻辑与行锁完全一致，你在 Celery 里直接 `import` 同步版调用即可。**同步版函数名没有 `_sync` 后缀**，就是 `trigger_video_generation` / `mark_work_succeeded` / `mark_work_failed_and_refund`。

#### 1）【我调你 · 任务入队】你需实现的 Celery Task 入口

考生点击生成并通过时段与配额原子扣减后，网关会自动把 `work_id` 塞进 Redis 等待队列 `i2v:queue:waiting_ids`，然后按 `CELERY_TASK_MODULE.CELERY_TASK_FUNC` 调用你的任务：

```python
# 例：backend/worker/tasks.py（与 .env 的 CELERY_TASK_MODULE=worker.tasks 对应）
@celery_app.task(name="i2v.generate_video")
def enqueue_video_generation(
    work_id: int,
    image_url: str,        # 已为你签好名的首帧图可读 URL（有效期 2 小时），直接传给 MiniMax
    prompt: str,           # 考生提示词（≤300 字）
    engine_code: str,      # "wq-v1"(768P) 或 "wq-v1-lite"(480P)
    duration: int = 15,
    aspect_ratio: str = "16:9",
):
    ...
```

> 注：当你的 Worker 真正抢到并发信号量开始跑任务时，请先执行 `ZREM i2v:queue:waiting_ids {work_id}`，并把 `works` 表该行的 `task_status` 更新为 `'running'`（同时写入 `upstream_task_id` 供对账用）。同步版 `handoff_sync.trigger_video_generation` 里的 Mock 线程演示了这套动作，可直接参考。

#### 2）【你调我 · 成功出片回调】`mark_work_succeeded`

当你从 MiniMax 拉取视频、经 `ffmpeg` 统一封装（15s / H.264+AAC / `moov` 前置便于边下边播）并调用 `app/core/storage.py::upload_bytes` 上传至私有桶 **`i2v-videos`**（对象路径规范：`videos/{candidate_account}/{task_no}.mp4`）后，直接调用：

```python
from app.services.handoff_sync import mark_work_succeeded

mark_work_succeeded(
    work_id=work_id,
    video_key=f"videos/{account}/{task_no}.mp4",  # 存入 i2v-videos 桶的 key
    file_size=mp4_bytes_length,
    duration_sec=15,
    cost_seconds=elapsed_sec,
    upstream_task_id=minimax_task_id,
)
```

#### 3）【你调我 · 最终失败与退配额回调】`mark_work_failed_and_refund`

当单任务满 **600 秒硬超时**、上游内容审核拦截、或 **指数退避重试 1 次后依然失败** 时，**必须且只能**调用此函数（内部已封装 `SELECT ... FOR UPDATE` 悲观行锁 + `quota_refunded` 幂等校验 + 自动回退 `quotas.quota_used`（`GREATEST(x-1, 0)`）+ 自动写入 `audit_logs`，哪怕你的僵尸任务对账脚本重复触发 10 次也绝不会多退配额！）：

```python
from app.services.handoff_sync import mark_work_failed_and_refund

mark_work_failed_and_refund(
    work_id=work_id,
    error_code="UPSTREAM_TIMEOUT",  # 或 CONTENT_FILTERED / API_ERROR / ENQUEUE_FAILED ...
    readable_error_msg="视频生成超时，系统已自动退还本题次数，请重新提交",  # 直接展示给考生
    retry_count=1,
)
```

### 第三步：读写约定的 4 个 Redis Key（驱动前端排队进度与后台健康监控）

前端每 3 秒轮询排队进度、管理员后台点「一键切 480P 降级」和查看「引擎标红告警」，全靠下面 4 个 Redis Key 自动联动（Redis `db=0`）：

1. **`i2v:queue:waiting_ids`（ZSet）**：网关入队时已自动 `ZADD`；你的 Worker **开始执行时务必 `ZREM(str(work_id))`**（成功/失败回调里也已兜底 `ZREM`）。前端用 `ZRANK` 算「前面还有 N 人」。
2. **`i2v:stats:avg_cost_sec`（String）**：你每成功跑完一单，请更新最近 20 单的滑动平均耗时秒数（如 `"115"`），网关读取它给考生计算「预计等待 X 分 X 秒」（默认 120）。
3. **`i2v:engine:config:{code}`（Hash）**：管理员在后台修改 `wq-v1` / `wq-v1-lite` 的并发上限 `max_concurrency`、启停状态或切换默认引擎时，后台已自动同步写入此 Hash（字段：`model / resolution / duration / max_concurrency / status / is_default`）。你的信号量限流器可直接读取它实现**不重启的动态调并发**。
4. **`i2v:engine:health:{code}`（Hash）**：请你在每次调用完 MiniMax 后更新字段 `recent_success`、`recent_failed`、`avg_cost_sec`、`circuit_open`（`0` 或 `1`），管理后台 `/console/engines` 页面会自动读取并在失败率 >20% 或熔断时标红展示。

---

## 4. 踩过的坑与绝对红线（新会话 / 联调绝对不要再踩！）

1. **【红线】严禁混用测试库与主库**：`pytest` 已在 `backend/tests/conftest.py` 中硬编码指向 `i2v_platform_test` 和 Redis `db=15`。任何新增测试严禁修改此隔离逻辑去 `TRUNCATE` 主库 `i2v_platform`！
2. **【红线】严禁引入 `passlib`**：Python 3.11+ 搭配新版 `bcrypt 5.0` 使用 `passlib` 会直接崩溃，全项目统一使用 `app/core/security.py` 中的原生 `bcrypt.hashpw / checkpw`。
3. **【红线】严禁按进程名粗暴杀 Python**：Windows 下停止服务必须运行 `.\scripts\stop_prod.ps1`，严禁执行 `Stop-Process -Name python` 误杀宿主机其他进程。
4. **【网关坑】前端管理路由与后端接口防撞**：管理端前端路由已固定为 `/console/*`，后端管理 API 为 `/admin/*`。新增管理页面必须在 `/console/*` 下扩展，严禁改回 `/admin/*`，否则浏览器按 F5 刷新页面会被 Nginx 直接打给 FastAPI 报 401/404 JSON！
5. **【网关坑】Nginx 长连接与突发限流参数勿删**：`nginx/nginx.conf` 中的 `worker_processes 1;`、`upstream keepalive 256;` 以及 `rate=100r/s burst=300 nodelay` 是消除 100 人整点洪峰下 Docker 网桥 `502` / `503` 的关键配置，严禁随意改小。
6. **【容器坑】官方 MinIO 镜像已下架 + 卷权限问题**：不要把 `docker-compose.yml` 里的镜像改回 `minio/minio:latest`（Docker Hub 已移除），保持当前的 `alpine/minio:RELEASE.2025-10-15T17-29-55Z` 及前置权限初始化容器 `minio-init` 不动。
7. **【云端上线坑】MiniMax 读取首帧图的公网可达性**：本地联调时 MinIO 地址是 `127.0.0.1:9000`，**公网上的 MiniMax 服务器无法访问你本机的 `127.0.0.1`！** 同伴真机联调 MiniMax API 时，把 `.env` 里这几项切到正式云存储即可，业务代码一行都不用改：

   ```env
   MINIO_ENDPOINT=<你的 OSS/COS 域名，如 bucket.oss-cn-hangzhou.aliyuncs.com>
   MINIO_ACCESS_KEY=<AK>
   MINIO_SECRET_KEY=<SK>
   MINIO_SECURE=true
   CDN_BASE_URL=<可选：CDN 域名，填写后签名 URL 的 host 会自动替换为 CDN>
   ```

   （或给本地 MinIO `9000` 端口挂一个临时 Cloudflare Tunnel / frp 公网域名。）

8. **【时区坑】数据库统一存 UTC**：`time_windows.start_at/end_at`、`works.submitted_at` 等都是 **naive UTC**，接口出参统一 ISO 8601 带时区。你写 Worker 时如需比对时间，请用 `app/core/timeutil.py::utcnow()`，不要用 `datetime.now()`。

---

## 5. 下一步联调上线计划（Checklist）

- [ ] **Step 1（同伴侧）**：同伴基于 `handoff_sync.py` 接入 Celery Worker + MiniMax API（建议先用 480P 短时长实测并发上限与提额情况）。
- [ ] **Step 2（存储切换）**：将 `.env` 中的本地 MinIO 切换为正式云厂商对象存储（OSS / COS）+ 开启 CDN 域名与 CORS `Range` 头放行。
- [ ] **Step 3（合流联调）**：将 `.env` 设为 `MOCK_WORKER=false`，用 `DEMO0001` 走通「考生传图 → 真实 MiniMax 生成 15s 视频 → 在线播放与下载 → 人为断网触发失败自动退还配额」全链路。
- [ ] **Step 4（正式部署）**：迁移至独立 8 核 Linux 云服务器，修改 `.env` 中的 `INIT_ADMIN_PASSWORD` 与 `AES_FERNET_KEY` 生产强密钥，正式交付开考。

---

## 6. 交接边界速查（给同伴的一页纸）

| 方向 | 调用 | 文件 | 说明 |
| :--- | :--- | :--- | :--- |
| 业务 → 你 | `enqueue_video_generation(work_id, image_url, prompt, engine_code, duration, aspect_ratio)` | 你实现，路径由 `.env` 的 `CELERY_TASK_MODULE/FUNC` 指定 | 入队后自动派发；`ZADD` 已由业务端完成 |
| 你 → 业务 | `mark_work_succeeded(...)` | `app/services/handoff_sync.py` | 成功出片回调，幂等 |
| 你 → 业务 | `mark_work_failed_and_refund(...)` | `app/services/handoff_sync.py` | 失败退还，行锁幂等，重复调用安全 |
| 你 → 业务 | `handoff_sync.trigger_video_generation(...)` | `app/services/handoff_sync.py` | 同步版契约 A（含 Mock 线程与 Celery 派发），可在 Celery 内部复用 |
| 共享状态 | `i2v:queue:waiting_ids` / `i2v:stats:avg_cost_sec` / `i2v:engine:config:{code}` / `i2v:engine:health:{code}` | Redis `db=0` | 见 §3 第三步 |
| 共享数据 | `works` 表状态机：`draft → queued → running → succeeded/failed` | `docs/handoff-contract.md` | 终态不可再变；`quota_refunded=1` 表示已退还 |

> 契约细节以 `docs/handoff-contract.md` 为准；本文档只做导航与联调指引。
