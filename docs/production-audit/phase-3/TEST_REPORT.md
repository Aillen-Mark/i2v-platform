# Issue 009 测试报告

运行环境为 Windows 本机、Pytest、FFmpeg 和 FFprobe 9.1。应用集成使用 MySQL 的 `i2v_platform_test`、独立 Redis DB 15 和隔离的 S3 兼容 Moto 端点。Redis 重启用单独的 Redis 7 Docker 容器。厂商行为用本地 HTTP 服务，媒体样本在临时目录由真实 FFmpeg 按需生成。表中的短名是同一全量命令中的 Pytest 测试名或参数组。

在 `backend/` 下执行，环境变量指向匹配的 FFmpeg/FFprobe 和独立测试服务。使用 Windows 时可设置 `I2V_TEST_REDIS_DOCKER='wsl -d Ubuntu -- docker'`。

```bash
python -m pytest -p no:cacheprovider tests worker/test_worker.py worker/test_media.py -rA -q --log-file phase3-app.log --log-file-level INFO
```

| 编号 | 测试与样本 | 预期 | 实际与证据 | 结果 |
| --- | --- | --- | --- | --- |
| T01 | `accept_valid_video_and_supply_aac`，15 秒有声 MP4 | 正常转码 | 探测到目标时长与两条预期编码流 | PASS |
| T02 | 同一测试，无音轨 15 秒 MP4 | 正常转码 | `media_validated`，成片通过探测和完整解码 | PASS |
| T03 | 同一测试，将成片 AAC 解至 PCM | 实际为静音 | PCM 非空，采样全零 | PASS |
| T04 | `reject_one_second_for_fifteen_second_task`，有声及无声 1 秒 | 拒绝 | `VIDEO_DURATION_MISMATCH`，实际 1.0000 秒 | PASS |
| T05 | `uses_work_duration_and_accepts_small_tolerance`，14.7 秒与目标 5 秒任务 | 容差内通过，支持非 15 秒 | 均生成通过校验的输出 | PASS |
| T06 | 同一测试，14.2 秒与 15.8 秒对 15 秒目标 | 拒绝 | 实测探测值为 14.2500 与 15.8333，均被拒绝 | PASS |
| T07 | `reject_missing_video_and_invalid_files`，乱码、空文件、30 MB 以上成片 | 拒绝 | `FFPROBE_EXIT` 和 `OUTPUT_SIZE` | PASS |
| T08 | 同一测试，只有 AAC 的文件 | 拒绝缺视频流 | `NO_VIDEO_STREAM` | PASS |
| T09 | `accept_valid_video_and_supply_aac`、`reject_wrong_resolution_and_final_codec`，MPEG-4 Part 2 和错误档位 | 转为 H.264，输出编码和分辨率受限 | 正常输入转码通过，未转码件和 480P/768P 错配件被拒绝 | PASS |
| T10 | `accept_valid_video_and_supply_aac`、`undecodable_audio_is_not_replaced_with_silence`，正常与损坏 AAC | AAC 合格才通过 | 正常为 AAC，破坏包经真实转码返回 `TRANSCODE_EXIT` | PASS |
| T11 | `reject_non_mp4_container`，重封装为 MOV | 拒绝非 MP4 | `OUTPUT_CONTAINER` | PASS |
| T12 | `complete_decode_detects_damage_after_mp4_header`，截断 faststart MP4 | 读得头也必须拒绝坏帧 | FFprobe 仍返回约 15 秒，完整解码触发 `DECODE_EXIT` | PASS |
| T13 | `ffmpeg_timeout_and_nonzero_are_failures`，故意实时运行 20 秒 | 2 秒期限终止 | `media_process_timeout stage=TRANSCODE` | PASS |
| T14 | 同一测试，真实 FFmpeg 打开不存在的输入 | 脱敏记录非零退出并失败 | `TRANSCODE_EXIT` 和执行日志 returncode | PASS |
| T15 | `real_ffprobe_killed_on_timeout`，真实 FFprobe HTTP 请求无响应 | 限时终止 | 确认请求已收到，并记录 `FFPROBE_TIMEOUT` | PASS |
| T16 | `disk_reserve_blocks_processing`，模拟磁盘剩 1 字节 | 禁止生成输出 | `MEDIA_DISK_SPACE`，无输出 | PASS |
| T17 | `killed_transcode_is_recovered_without_new_provider_request`，崩溃遗留与新目录 | 回收孤儿并保留在用目录 | `media_orphan_cleanup`，成功后独立工作区为空 | PASS |
| T18 | `interrupted_download_stops_before_transcode`，HTTP body 小于 Content-Length | 中断后不进入转码 | `DOWNLOAD_INTERRUPTED`，无输出文件 | PASS |
| T19 | `partial_upload_preserves_work_and_streams_file`，S3 PUT 收一部分后断 TCP | Work 保持运行，后续可恢复 | `OBJECT_UPLOAD_FAILED`，没有视频地址或退款，重新处理后成功 | PASS |
| T20 | 同一测试，检查 SDK 接收的真实文件对象 | 无完整字节参数，大小一致 | 上传入口收到可 seek 文件对象，检查了真实文件描述符和大小 | PASS |
| T21 | `killed_transcode_is_recovered_without_new_provider_request`，看到真实输出文件后强杀 Worker | 媒体进程退出，原状态可接续 | 子进程强杀，媒体守卫停止，原尝试仍为 uploading | PASS |
| T22 | 同一测试，等待真实租期过期 | 新世代接管 | 世代 2 死亡，世代 3 接管并写入独立对象 | PASS |
| T23 | `concurrent_claim_heartbeat_and_stale_writes`，过期后旧租约写成片、检查点和状态 | 旧写入被拒，新租约保持有效 | 多处 `ExecutionStopped`，新世代继续结算 | PASS |
| T24 | `killed_transcode_is_recovered_without_new_provider_request`，已知 ID | 恢复只查原任务 | 本地服务收到查询及下载各一次，POST 为零 | PASS |
| T25 | `unplayable_legacy_checkpoint_cannot_succeed_or_refund`，快启头部完整但媒体截断的旧对象与 1 秒源片 | 人工核对，保留配额 | `media_checkpoint_rejected reason=DECODE_EXIT`，`VIDEO_DURATION_MISMATCH`，原 ID 与额度保持 | PASS |
| T26 | `concurrent_claim_heartbeat_and_stale_writes` 与既有退款回归 | 明确失败重复通知退一次 | 额度只回补一次，审计记录为 1 | PASS |
| T27 | `force_kill_and_takeover[uploading]`，验证过的对象上传后、成功提交前强杀 | 后续复用对象 | 新世代按检查点重验真并成功，无新增供应商请求 | PASS |
| T28 | `database_unavailable_cannot_mark_uploaded_work_successful`，连接关闭 TCP 端口 | 写库失败不确认，恢复后可确认 | 捕获真实 MySQL 连接错误，恢复对象并提交成功 | PASS |
| T29 | `heartbeat_loss_kills_running_ffmpeg` 与 `celery_entry_renews_lease_and_stops_when_redis_slot_disappears` | 心跳失效终止媒体进程 | 真实 FFmpeg 退出，实际 Redis 槽失效触发 `execution_heartbeat_lost` | PASS |
| T30 | `killed_transcode_is_recovered_without_new_provider_request`，预先老化两个世代目录 | 只回收旧目录 | 新世代的目录仍存在并可用，运行完成后自行清理 | PASS |

最终全量回归 **101 项通过，203.23 秒**，零失败、零跳过。真实首帧和成片端到端用例经过本地 HTTP 上游替身及 S3 兼容端点，检查了最终 Work 与配额。Ruff、17 个 Python 文件语法检查和 Git 空白检查均通过。真实 MiniMax、生产 MinIO 和 Ubuntu 24.04 目标机验证均为 BLOCKED，隔离回归无此项依赖。WSL Ubuntu 26.04 对 Linux 父进程死亡守卫进程做了额外验证，真实 Linux 子进程在父进程强杀后退出，测试子进程使用 `sleep`。真实 FFmpeg 强杀用例在 Windows 运行。

## 单任务资源抽样

用本地 FFmpeg 生成 1280×720、24 fps、15 秒无音轨的动态样本。运行实际 `transcode` 和完整解码，通过 psutil 每 35 ms 采集 Python、工具进程及其子进程树。输入 4,883,735 字节，输出 5,517,054 字节，整段 10.38 秒，进程树峰值 RSS 约 294.5 MiB，采样 CPU 时间约 16.41 秒。包含 Python 启动、两次 FFprobe、转码和完整解码。单机单任务样本不代表目标服务器吞吐量。


T20 额外使用真实 boto3 SDK 和同一隔离 S3 端点上传 8 MiB 的磁盘测试文件。SDK 调用文件读取 83 次，单次请求最大 1,048,576 字节，未出现无界读取。上传完成后核对对象大小并移除本次新建的测试对象与测试桶。
