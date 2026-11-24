# 部署与回滚

1. 确认第二阶段的 001–003 迁移已存在。第三阶段无新增数据库迁移。部署前备份并盘点活动 Work、上游 ID 和对象检查点。
2. 在 Ubuntu 安装同一发行包的 FFmpeg 与 FFprobe，确认有 libx264、AAC、lavfi 和 MP4 muxer。在 Linux 的生成 Worker 与恢复 Worker 运行用户下验证两条命令。保留媒体进程守卫模块。
3. 配置 `I2V_FFMPEG` 与 `I2V_FFPROBE` 可执行文件路径。默认走 PATH。可将 `I2V_MEDIA_TMP_DIR` 指向 Worker 专用的本地文件系统，两类 Worker 使用同一目录。目录由运行账户管理，按不同应用或数据库实例隔离。每个执行目录预算约 130 MB。700 权限适用于 Linux 新建目录，Windows 继承配置目录的 ACL。之外保留至少 256 MiB 可用空间。开始用单个生成进程、并发 1、预取 1、编码线程 2，保留独立恢复 Worker 与单个 Beat。
4. 暂停提交并按第二阶段顺序替换 Worker 与恢复组件。在受控环境验证下载、转码、完整解码、对象读回以及最终成功回写，观察媒体事件和人工核对队列后恢复提交。[Worker 运行说明](../../../backend/worker/README.md)
5. 回滚先暂停生成消费与恢复，保留 MySQL 记录和对象。若需切回第二阶段 Worker，先盘点所有未完成 uploading 检查点和待处理的异常媒体，通过验真或运营核对后再允许旧版接管。旧版没有媒体成功门槛。应用回滚无须删表，保留审计记录和有效视频。升级不会重写已有成功作品。

人工核对沿用三个位置参数和确认标记，追加安全的证据编号。操作人来自 OS 用户；共享账户只记录该账户，无法区分背后的个人。

```bash
python -m worker.generation_reconciler WORK_ID REQUEST_ID TASK_ID --confirm-provider-correlation --reason INC-123
```

当前改动保留在源码工作区。本轮验收使用隔离数据库、Redis 和对象端点。
