# Issue 008 当前代码审计

本地基线 913c158、工作树 9bba477、工作区干净。上一阶段 GitHub 提交 c225fc3 对应同一工作树。本轮附件是开发指令文本，未提供独立源码 ZIP；审计以 Git 当前文件为准。

| 项 | 实查 |
| --- | --- |
| 1–2 桶与内容 | 默认 `i2v-public` 保存考生首帧和题目参考图，`i2v-videos` 保存成片。启动时只确保这两个配置桶存在。 |
| 3–5 对象区分 | 真实成片使用 `videos/generated/<work_id>/<task_no>-<generation>.mp4`。Mock 使用 `videos/mock/`。图片分别由作品创建和管理员上传写入 public 桶。历史真实 Key 保留。 |
| 6 引用 | `Work.image_key` 引用首帧，`Work.video_key/file_size` 引用作品成片，`Question.ref_image_key` 引用题目参考图。 |
| 7 成功写入 | pipeline 的 `finish` 调用同步结算，在锁定 Work、执行权和尝试后原子写成功及 Key、大小。旧 Mock 另有同步与异步回调。 |
| 8–9 检查点写入与保留 | artifacts 上传之前将 Key、长度和 SHA-256 写在该 Work 的 GenerationExecution。成功只释放租约字段，摘要保留。 |
| 10 历史 | 002/007 迁移为扩展建表，历史和 Mock 可以没有可信摘要；Work 本身只存 Key 与大小。 |
| 11 回写异常 | 下一世代读取原检查点，检查 HEAD 和完整 SHA-256，下载临时文件做媒体验真，然后通过有效租约提交。 |
| 12 删除检测 | 单条下载和 ZIP 在请求时做 HEAD。缺少后台批量发现流程。 |
| 13 损坏检测 | 上传和未结算恢复时完整回读。已成功下载与作品列表无持续 SHA-256 核验。 |
| 14–16 版本和备份 | 当前 Compose 只有本地 MinIO 数据卷。无启用版本控制、跨实例复制或异地对象备份的代码配置。无法由源码判断外部环境是否另行配置。 |
| 17 共同恢复点 | 005 对 InnoDB 使用快照，归档带 SHA-256、服务端设置以及开启 Binlog 时的坐标，支持恢复新库。与对象恢复点没有自动关联。 |
| 18–20 下载 | S3 GET 预签名，CDN 替换 scheme/host 并保留原查询；CDN 回源需保留签名的源 Host。单条 API 返回链接或 302，ZIP 按块传输。Range 由 S3/CDN 处理。业务层不使用 ETag 验证。 |
| 21 共享引用 | Work.video_key 无唯一约束；多个 Work 可引用同一 Key。同一 Work 的成片/检查点同 Key 是预期关系。 |
| 22 世代隔离 | 新生成 Key 包含 Work ID 和租约世代，旧 Worker 无法用同一 Key 覆盖新世代。 |

证据 [对象存储](../../../backend/app/core/storage.py) · [成片上传和恢复](../../../backend/worker/artifacts.py) · [资源回写](../../../backend/app/services/handoff_sync.py) · [下载](../../../backend/app/api/works.py) · [005 数据库备份](../../../deploy/ubuntu/mysql-backup.md)。

人工绑定入口允许普通 UNKNOWN 的 failed/cancelled 上游 ID 通过查询校验并落 accepted。本轮在提交事务前拒绝终止状态，保留 MEDIA_FAILED 只能接续已成功任务的规则。
