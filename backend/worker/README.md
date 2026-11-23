# 图生视频 Worker

Worker 从现有 FastAPI 交接入口接收任务，使用 MiniMax H3 V2 生成视频，将 H.264 + AAC MP4 上传到私有桶，再调用业务端的成功或退额回调。它从对象存储读取首帧，按考生所选画幅居中裁剪并内联发送给 H3。H3 首帧模式根据实际图片决定画幅。

## 联调启动

FastAPI 和 Worker 使用同一个仓库根目录的 `.env`，并连接同一套 MySQL、Redis 和对象存储。安装依赖时使用 FastAPI 的 Python 环境。

```bash
python -m pip install -r backend/worker/requirements.txt
```

在根目录的本地 `.env` 中，保留现有连接配置并设置以下变量。将 MiniMax API Key 写入本地环境变量或此文件。

```dotenv
MOCK_WORKER=false
CELERY_TASK_MODULE=worker.tasks
CELERY_TASK_FUNC=enqueue_video_generation
MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimax.cn
I2V_ACCOUNT_CONCURRENCY=30
```

Linux 或 WSL 中安装 FFmpeg（需包含 libx264 和 AAC），然后从 `backend/` 目录运行 Worker 和一个 Beat 进程。并发数可按部署机器调整，Redis 同时执行账号总并发和单引擎并发限制。

```bash
python -m celery -A worker.tasks:celery_app worker -Q i2v_generate --pool=prefork --concurrency=25 --loglevel=INFO
python -m celery -A worker.tasks:celery_app beat --schedule=/tmp/i2v-worker-beat --loglevel=INFO
```

本机调试可用 `--pool=solo --concurrency=1`。进程级硬超时需要 Linux 或 WSL 的 prefork。若 FFmpeg 不在 PATH，设置 `I2V_FFMPEG` 为可执行文件路径。修改 `.env` 后重启 FastAPI 和 Worker。

Worker 在 600 秒后结束生成等待。上游仍在排队时尝试取消，上游已经在运行时按 MiniMax 接口限制走本地失败退额。上游任务创建响应丢失时直接退额，避免重发已可能计费的请求。失败回调使用现有 MySQL 事务的幂等退额规则。

## 测试

从 `backend/` 目录运行 `python -m pytest worker/test_worker.py -q`。测试不消耗 API 配额。真实出片验收需使用已开通 H3 V2 的账号和有效余额。
