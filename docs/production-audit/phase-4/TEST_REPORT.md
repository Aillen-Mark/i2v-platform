# Issue 008 测试报告

在 `backend/` 执行以下命令。环境为 Windows、隔离 MySQL 8.0 测试库、Redis DB 15、FFmpeg/FFprobe 9.1 和本地 Moto S3 协议端点。boto3 通过真实 HTTP 访问测试桶。对象读写、损坏模拟和删除只针对本次生成的隔离测试桶。MySQL 演练使用 `mysqldump`、`mysql` 和独立临时数据库。真实 MinIO 与真实异地故障域的验收单列。

```bash
python -m pytest -p no:cacheprovider tests worker/test_worker.py worker/test_media.py -rA -q --log-file phase4-app.log --log-file-level INFO
```

配置 `I2V_TEST_MYSQL_BIN` 指向 MySQL 客户端目录，`I2V_FFMPEG` 和 `I2V_FFPROBE` 指向真实二进制。Redis 故障测试通过 `I2V_TEST_REDIS_DOCKER='wsl -d Ubuntu -- docker'` 创建自己的容器。也可通过 `I2V_TEST_BACKUP_ENDPOINT` 为测试提供另一独立 S3 兼容服务端点。

| 项 | 用例与证据 | 结果 |
| --- | --- | --- |
| T01 | `new_work_runs_real_first_frame_and_media_pipeline` 按实际首帧、转码、S3 上传与 MySQL 回写完成。 | PASS |
| T02 | `real_backup_restore_and_seals` 核对源、目标及恢复对象长度。 | PASS |
| T03 | 同上，`ObjectDigest` 整体相等，含长度和 SHA-256。 | PASS |
| T04 | `partial_upload_preserves_work_and_streams_file` 发送真实 PUT 后关闭 TCP，Work 保持 running，随后恢复。 | PASS |
| T05 | `reconciliation_reports_real_s3_and_mysql_connection_failures` 对实际关闭端口分类失败。真实 MinIO 自身重启另列。 | PASS |
| T06 | `database_unavailable_cannot_mark_uploaded_work_successful` 对象验证后连接 MySQL 失败，Work 未成功。 | PASS |
| T07 | 同上，恢复 DB 连接后复用已上传且重新媒体验真的对象。 | PASS |
| T08 | `killed_transcode_is_recovered_without_new_provider_request` 接管后配额仍仅用一次，只有复用已知任务的 GET。 | PASS |
| T09 | `concurrent_claim_heartbeat_and_stale_writes` 旧世代上载与结算抛出 ExecutionStopped。 | PASS |
| T10 | `inventory_detects_anomalies_and_resumes_without_mutation` 同长度替换对象产生 CHECKSUM_MISMATCH。 | PASS |
| T11 | 同一用例，可信 SHA-256 + 大小一致产生 HEALTHY。 | PASS |
| T12 | 同一用例，删除测试对象后产生 MISSING_OBJECT。 | PASS |
| T13 | 同一用例，改变实际对象数据产生 CHECKSUM_MISMATCH。 | PASS |
| T14 | 同一用例，数据库/HEAD 大小不一致产生 SIZE_MISMATCH。 | PASS |
| T15 | 同一用例，推进观察时间，未引用 generated Key 产生 ORPHAN_OBJECT。 | PASS |
| T16 | 同一用例，活动检查点及同 Work 的生成 Key 为 ACTIVE_CHECKPOINT。 | PASS |
| T17 | 同一用例，无可信摘要产生 LEGACY_UNVERIFIED。 | PASS |
| T18 | 真实关闭的 HTTP 端口、403/500 测试端点、缺失 Bucket 均为 STORAGE_UNAVAILABLE。 | PASS |
| T19 | PyMySQL 连接关闭的实际 TCP 端口，DATABASE_UNAVAILABLE 且无孤儿计数。 | PASS |
| T20 | 同 Key 绑定两个测试 Work，报告 REFERENCE_CONFLICT。 | PASS |
| T21 | 以两条每页扫描超过一页，续接游标覆盖所有对象。 | PASS |
| T22 | 比较 T11–T21 前后源 Bucket 清单，完全一致。 | PASS |
| T23 | 对账后数据库成功与活动 Work 数、状态一致。 | PASS |
| T24 | 核对 JSON summary 和 CSV 发现记录数及分类。 | PASS |
| T25 | `real_backup_restore_and_seals` 将有效视频上传到独立隔离备份桶，COMPLETE。 | PASS |
| T26 | 同一用例，明确比较目标对象长度。 | PASS |
| T27 | 同一用例，独立回读并比较 SHA-256。 | PASS |
| T28 | `backup_retries_after_real_s3_connection_drop` 接收部分上传后关闭实际连接，有限重试后成功。 | PASS |
| T29 | 缺一对象的备份为 PARTIAL，远端不发布密封。 | PASS |
| T30 | 空配置返回 OFFSITE_NOT_CONFIGURED。 | PASS |
| T31 | 密封和清单摘要双校验，替换清单后拒绝恢复。 | PASS |
| T32 | 重跑全量清单时核对目标字节并记录 skipped。 | PASS |
| T33 | 预存同长错误内容后停止，冲突对象原样保留。 | PASS |
| T34 | 同摘要备份去重，实际写入次数降低。 | PASS |
| T35 | 备份目标连接真实关闭端口时备份失败，回读原件 SHA-256 一致。 | PASS |
| T36 | 新隔离桶恢复正确，目标 Key 与旧 Key 一致。 | PASS |
| T37 | 恢复后 HEAD 与逐块长度一致。 | PASS |
| T38 | 恢复后完整回读摘要相等。 | PASS |
| T39 | 目标冲突保持原样，条件 PUT 的 412 防止竞争覆盖。 | PASS |
| T40 | 篡改 JSONL 后由摘要校验拒绝。 | PASS |
| T41 | PARTIAL 批次无法被 restore 接受，即使开启本机演练。 | PASS |
| T42 | 从目标桶读回的媒体样本经真实 FFprobe 验证。 | PASS |
| T43 | 同一目标样本通过完整影音解码。 | PASS |
| T44 | MySQL dump 恢复至独立测试库，用清单与 Work ID、Task No、Key、大小和时长逐行匹配。下载的备份 SQL 包摘要与已导入快照一致。 | PASS |
| T45 | 原有 `secure_download_and_range_resume` 验签名、授权和 302。 | PASS |
| T46 | 签名请求 Range 0–1023，206，实际 1024 字节。 | PASS |
| T47 | 签名请求中间 Range 6144–7167，206 和精确 Content-Range。 | PASS |
| T48 | 超出大小的 Range 416。 | PASS |
| T49 | 验证 CDN 替换 scheme/host，保留 path/query。实际 CDN 回源另列。 | PASS |
| T50 | 下载响应中文作品名称及 RFC 5987 参数通过。 | PASS |
| T51 | 测试端点无法证明签名过期的原生拒绝语义，未配置实际 MinIO。 | BLOCKED |
| T52 | 8 MiB 通过预签名直连 S3 端点分块读取，API 回归确认仅返回元数据或跳转。 | PASS |
| T53 | 普通未知请求绑定 failed 被拒，状态不变。 | PASS |
| T54 | 普通未知请求绑定 cancelled 被拒，状态不变。 | PASS |
| T55 | 普通未知请求绑定 queued、running、succeeded 符合预期。 | PASS |
| T56 | MEDIA_FAILED 仅接受 succeeded 并回到 uploading，拒绝另外四种状态，配额不变。 | PASS |

全量回归 **119 通过，0 失败，0 跳过，214.84 秒**。最终对账游标刷新和实际截断 PUT 用例更新后，重跑 `python -m pytest -p no:cacheprovider tests/test_object_operations.py -q -rA`，**9 通过，0 失败，33.76 秒**。两次运行有重叠，不合计。未配置目标和非法对账页大小的 CLI 冒烟均返回 3。

真实 MinIO、Provider 端私有权限/原生校验和、真实异地 S3 故障域、目标机器与实际 5 Mbps 公网链路、真实 CDN 回源、目标站点 RPO/RTO 均为 BLOCKED。Docker Hub 配置代理连接超时，官方归档直链返回 410。已完成的本机测试不计作异地验收。
