# 对象存储架构

| Bucket | 内容 | 生命周期 |
| --- | --- | --- |
| `i2v-public` | 考生首帧与题目参考图 | 业务引用，未纳入本轮视频异地备份 |
| `i2v-videos` | Mock 和真实成片 | Work 成功记录及 GenerationExecution 检查点作为清单来源 |
| 备份 Bucket | 内容寻址的 SHA-256 对象、JSONL 清单、密封文件、可选 MySQL 归档 | 由独立 S3 目标的生命周期策略管理 |

真实成片对象 Key 继续使用 `videos/generated/<work_id>/<task_no>-<lease_generation>.mp4`。备份对象使用 `<prefix>/data/sha256/<sha256>`，同一内容可以被多个 Work 清单引用而只复制一次。备份工具不把 ETag 当摘要。

`ObjectStore` 统一了分页、HEAD、流式 GET、条件创建和 S3 错误分类。传输使用 256 KiB 读取块、总时间预算和可选带宽预算。原有签名下载、CDN Host 替换、Range 直出和 ZIP 流式下载没有改变。
