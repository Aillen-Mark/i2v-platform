请先阅读 `CLAUDE.md`、`docs/handoff-contract.md` 和 `docs/progress.md`。

现在开始 **Phase 5：与生成模块联调准备 + 压测**。不要实现真实 Worker。

1. 自查 `handoff.py` / `handoff_sync.py` 与契约逐条一致，输出一份对照清单。为同伴写 `docs/worker-integration.md`：如何安装依赖、如何导入 `handoff_sync`、Celery 任务名与队列名、Redis Key、本地如何用 `MOCK_WORKER=false` 联调。
2. 写一个最小的 `backend/scripts/fake_celery_worker.py`：用真正的 Celery 消费 `i2v_generate` 队列，sleep 后调用 `handoff_sync` 的契约 B/C，验证 `MOCK_WORKER=false` 链路可用。
3. 压测脚本（locust 或 asyncio + httpx）：100 个考生同时登录并提交生成；100 个并发下载。输出各接口 P50/P95 耗时，对照需求 5.5 性能指标给出结论。
4. 上线检查清单 `docs/go-live-checklist.md`：环境变量、对象存储 CORS 与 Range、CDN、备份、默认管理员改密、日志不含明文密码、存储余量告警。

更新 `docs/progress.md` 并汇报。
