# 图生视频 Worker

Worker 从 MySQL 读取已提交的作品，用 MiniMax H3 V2 生成视频，将 H.264 + AAC MP4 保存到私有桶，并在业务事务中写入成功或退额结果。Worker 从对象存储读取首帧，按考生所选画幅裁剪后内联发送给 MiniMax。

## 联调与运行

FastAPI、Worker、Beat 和 Dispatcher 使用同一套 MySQL、Redis、对象存储和仓库根目录的 `.env`。使用 FastAPI 的 Python 环境安装 Worker 依赖。

```bash
python -m pip install -r backend/worker/requirements.txt
```

真实生成需设置 `MOCK_WORKER=false`、`MINIMAX_API_KEY`、`MINIMAX_BASE_URL`（默认 `https://api.minimax.cn`），以及 `CELERY_TASK_MODULE=worker.tasks`、`CELERY_TASK_FUNC=enqueue_video_generation`。通过本地环境变量或未纳入版本控制的 `.env` 提供密钥。Linux 或 WSL 需安装带 libx264 和 AAC 的 FFmpeg，同套安装中需包含 FFprobe。工具不在 PATH 时分别设置 `I2V_FFMPEG`、`I2V_FFPROBE`。

在 `backend/` 下分别运行以下长期进程。生成 Worker 可先用并发 1，生产并发按实际压测调整。单独启动一个恢复 Worker，使扫描不被长时间的视频任务占住。每套共享数据库和队列只启动一个 Beat。

```bash
python -m worker.outbox_dispatcher
python -m celery -A worker.tasks:celery_app worker -Q i2v_generate --hostname=generation@%h --pool=prefork --concurrency=1 --loglevel=INFO
python -m celery -A worker.tasks:celery_app worker -Q i2v_recovery --hostname=recovery@%h --pool=prefork --concurrency=1 --loglevel=INFO
python -m celery -A worker.tasks:celery_app beat --schedule=/tmp/i2v-worker-beat --loglevel=INFO
```

本地可用 `--pool=solo --concurrency=1` 调试。验证进程级 Celery 硬超时需用 Linux 或 WSL 的 prefork。

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `I2V_LEASE_SECONDS` | 60 秒 | MySQL 租约及 Redis 并发槽有效期 |
| `I2V_HEARTBEAT_SECONDS` | 15 秒 | 续租频率，必须小于租期的一半 |
| `I2V_MEDIA_TMP_DIR` | 系统临时目录下的 `i2v-media` | 两个 Worker 可访问的本机临时空间，按尝试及租约隔离 |
| `I2V_POLL_INTERVAL_SECONDS` | 5 秒 | MiniMax 查询间隔 |
| `I2V_RECONCILE_MAX_HOURS` | 6 小时 | 已知任务在同一尝试状态停留的人工核对阈值 |
| `I2V_ACCOUNT_CONCURRENCY` | 30 | Redis 中的账号级并发槽上限，生产初始值按并发预算设置 |

单次执行片软限时为 600 秒，Celery 硬限时为 645 秒。已知 ID 继续查询原任务。请求结果未知或本地执行超时，配额保持原状，等待恢复或人工核对。

## 迁移和人工核对

已有数据库按 001、002、003 顺序执行 [SQL 迁移](../../docs/migrations)，已确认应用的版本可跳过。空库使用 [初始化脚本](../../docs/schema.sql)。切换前暂停提交并停掉旧 Worker、Beat 和 Dispatcher，先备份并盘点活动任务。迁移为扩展式建表。切换和回滚步骤见[实施报告](../../docs/production-audit/phase-2/IMPLEMENTATION_REPORT.md)。

`generation_manual_review` 提供 Work、尝试、请求及已知供应商任务的标识。MiniMax 查询接口需要供应商任务 ID。只有运营人员已经通过独立证据确认某个上游任务与本地请求相符时，才能通过受控 Shell 录入该 ID。命令会向供应商核验 ID 与状态，并禁止覆盖已有的不同 ID。

```bash
python -m worker.generation_reconciler WORK_ID REQUEST_ID TASK_ID --confirm-provider-correlation --reason INC-123
```

缺少关联证据时保留人工核对状态。绑定会记录操作系统账号、证据编号和状态变化。恢复进程继续派发有明确上游任务 ID 的工作。媒体验真、磁盘预算和第三阶段回滚见[实施报告](../../docs/production-audit/phase-3/IMPLEMENTATION_REPORT.md)。
