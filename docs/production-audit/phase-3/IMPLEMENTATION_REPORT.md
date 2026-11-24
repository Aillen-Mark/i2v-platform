# Issue 009 实施报告

本轮在 e795b20 上实现了下载、探测、静音音轨、严格转码、成片验真、流式上传与故障恢复。确认上游成功后，验证失败会保留原任务 ID。可恢复错误继续接管，确定性媒体错误进入人工核对。公开 Work 状态、对象 Key 规则、配额规则和下载 API 延续既有实现。本轮复用 002/007 的表与检查点，无新增迁移。

## 文件与用途

| 文件 | 改动理由 |
| --- | --- |
| `backend/worker/media.py` | 流式写入并计算源摘要，检查传输完整性和磁盘空间。读取输入流、按需补静音，输出 H.264/AAC MP4。 |
| `backend/worker/media_validation.py` | 使用 FFprobe 验证实际流时长、分辨率、流数、AAC、H.264 和 MP4，再严格完整解码。 |
| `backend/worker/media_process.py`、`media_child.py` | 参数数组、分阶段超时、租约丢失检测、限制 stdout、退出码日志和父进程死亡保护。 |
| `backend/worker/media_workspace.py` | 优先使用独立磁盘位置，工作目录按尝试/执行世代隔离。正常退出清理，硬杀后通过年龄、锁和数据库状态回收。 |
| `backend/worker/pipeline.py`、`artifacts.py` | 验真后上传。对象续接时按旧检查点核对并重验真，成功回写仍验证数据库租约。 |
| `backend/app/core/storage.py` | 提供以文件对象为请求体的上传入口，避免增加完整视频内存副本。 |
| `backend/worker/config.py`、`tasks.py` | FFprobe 和工作目录配置、启动检查，恢复任务调用孤儿清理。 |
| `backend/worker/attempts.py`、`state.py`、`generation_reconciler.py` | 持久化状态和恢复时间使用 MySQL UTC，人工绑定时原子写现有 AuditLog。 |
| `backend/worker/test_media.py`、`backend/tests/test_media_recovery.py` | 真实媒体工具、HTTP/S3 中断、硬杀、旧检查点和故障恢复。 |
| `backend/tests/test_generation_recovery.py`、`test_generation_attempts.py` | 原有恢复用例改用有效媒体作为成功夹具，增加对账持久化断言。 |
| `backend/worker/README.md`、`docs/progress.md`、`docs/production-audit/phase-3/` | 工具配置、工作目录、上线与回滚、测试证据。 |

## 关键取舍

- 输入探测限制短片，输出也按流时长校验。选择 ±0.5 秒容差；保持整数业务时长字段，实际探测值另外记录。分辨率用已知引擎档位兼容范围，保留动态画幅。
- FFmpeg 使用两条编码线程、faststart 和严格错误退出。所有成片执行完整影音解码。识别无音轨后生成等时长静音声道，已有坏音轨进入错误流程。
- 进程使用局部单调截止。Linux 启用父进程死亡信号和进程组终止。Windows 通过 Job Object 让媒体进程随 Worker 或守卫退出。只记录必要的固定分类和退出码。下载与上传按块处理并在可取消边界检查租约。对象命名继续隔离旧世代。
- 人工对账在绑定同一事务写审计，包括 OS 身份、证据编号、前后状态以及供应商核验结果。共享 OS 账户场景的个人身份追责仍是部分完成，需要独立登录和相应证据。供应商只验证该任务 ID 和状态，任务归属依据由操作者独立查证。

## 验证与后续

最终全量回归 101 项通过，覆盖 T01–T30、原有 Outbox、Generation Attempt、Execution Lease、配额退款与 Work API。测试详情、命令及性能记录见[测试报告](TEST_REPORT.md)。配置、迁移和回退见[部署说明](DEPLOYMENT_NOTES.md)。尚待真实 MiniMax、MinIO 和目标 Ubuntu 24.04 机器联合验收。下一阶段 Issue 008 可用现有检查点和媒体规范设计对象对账，持续容量验证仍由后续阶段覆盖。
