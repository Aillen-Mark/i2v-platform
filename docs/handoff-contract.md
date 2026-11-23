# 业务端 ↔ 生成模块 交接契约

> 业务端（本仓库执行端）与生成模块（同伴）只通过本契约交互，双方都不直接修改对方负责的字段与逻辑。修改契约需双方确认。

## 1. works.task_status 状态机

| 状态 | 含义 | 写入方 | 触发 |
|---|---|---|---|
| draft | 已上传图片、未提交生成 | 业务端 | `POST /api/works` 审核通过后创建 |
| queued | 已扣配额、已入队 | 业务端 | `POST /api/works/{task_no}/generate` 事务成功 |
| running | 已占用并发位、正在调用模型 | 生成模块 | 拿到 MiniMax `upstream_task_id` 后更新 |
| succeeded | 成片已转码并上传对象存储 | 生成模块 → 调用契约 B | |
| failed | 重试后仍失败 / 600 秒超时 / 入队失败 | 生成模块或业务端 → 调用契约 C | |

只允许：draft→queued→running→succeeded/failed，或 queued→failed。终态不可再变。

## 2. 契约 A：业务端调用生成模块（入队）

业务端在**提交事务 commit 之后**调用：

```python
enqueue_video_generation.delay(
    work_id: int,
    image_url: str,        # 业务端签发的临时读链接（有效期 ≥ 2 小时），供 MiniMax 拉取首帧
    prompt: str,
    engine_code: str,      # 来自 engines 表当前默认启用引擎，如 "wq-v1"
    duration: int = 15,
    aspect_ratio: str = "16:9",
)
```

- Celery 任务名：`i2v.generate_video`；队列名：`i2v_generate`。
- 业务端负责 `ZADD i2v:queue:waiting_ids {work_id: 入队时间戳}`；生成模块开跑时 `ZREM`。
- 入队抛异常时业务端自行调用契约 C（`error_code="ENQUEUE_FAILED"`）。

## 3. 契约 B：成功回调（业务端实现，生成模块调用）

```python
mark_work_succeeded(
    work_id: int,
    video_key: str,        # videos/{account}/{task_no}.mp4
    file_size: int,
    duration_sec: int,
    cost_seconds: int,
    upstream_task_id: str,
) -> None
```

- 仅当当前状态为 queued/running 时更新，重复调用无副作用。

## 4. 契约 C：失败退还（业务端实现，生成模块调用）

```python
mark_work_failed_and_refund(
    work_id: int,
    error_code: str,           # UPSTREAM_TIMEOUT / UPSTREAM_ERROR / MODERATION_REJECTED / ENQUEUE_FAILED ...
    readable_error_msg: str,   # 给考生看的中文原因
    retry_count: int = 1,
) -> None
```

单事务内：锁 works 行 → 已退还或已成功则直接返回 → 更新为 failed、`quota_refunded=1` → `quota_used = GREATEST(quota_used-1, 0)` → 写 `audit_logs`（`admin_id=0`, `admin_username='SYSTEM_WORKER'`, `action='refund_quota'`）。重复调用无副作用（对账脚本可放心重试）。

## 5. 同步 / 异步两套实现

Celery Worker 是同步进程，不能直接复用 FastAPI 的异步引擎。业务端提供两套**逻辑完全一致**的实现：

| 文件 | 使用方 | 数据库驱动 |
|---|---|---|
| `backend/app/services/handoff.py` | FastAPI、Mock Worker | SQLAlchemy async + aiomysql |
| `backend/app/services/handoff_sync.py` | 同伴的 Celery Worker | SQLAlchemy sync + pymysql |

两者共用同一份测试用例，保证行为一致。

## 6. Redis Key 约定

| Key | 类型 | 写入方 | 读取方 | 说明 |
|---|---|---|---|---|
| `i2v:queue:waiting_ids` | ZSet | 业务端 ZADD / 生成模块 ZREM | 业务端 | ZRANK 得到前面排队人数 |
| `i2v:stats:avg_cost_sec` | String | 生成模块（最近 20 条滑动平均，默认 120） | 业务端 | 预计等待 = ceil(前面人数 / 并发上限) × 平均耗时 |
| `i2v:engine:config:{code}` | Hash | 业务端（后台修改时刷新） | 生成模块 | 字段：model, resolution, duration, max_concurrency, status, is_default |
| `i2v:engine:health:{code}` | Hash | 生成模块 | 业务端 | 字段：recent_success, recent_failed, avg_cost_sec, circuit_open |

## 7. 生成模块需遵守

- 单任务 600 秒硬超时；失败自动重试 1 次（指数退避 + 抖动）；最终失败必须调用契约 C。
- 成片上传到同一个存储桶的 `videos/` 前缀，输出 15 秒、MP4（H.264 + AAC）、≤30MB。
- 对账脚本扫描长时间 queued/running 的任务，查询上游终态后调用契约 B 或 C。
