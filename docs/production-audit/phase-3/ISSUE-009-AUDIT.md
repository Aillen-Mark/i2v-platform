# Issue 009 代码审计

基线 e795b20，工作区无改动。真实 Worker 交接位于 app/services；worker/ 下无同名 handoff 文件。现有 Outbox 的首收件与领取执行租约原子提交，尝试状态、退款和成片检查点均已落 MySQL。

| 项 | 基线实查 |
| --- | --- |
| 1 下载 | `prepare_video` 在 HTTPX 流中写临时 source.mp4，检查 HTTP 状态、100 MB、执行截止；尚无摘要与独立下载预算。 |
| 2 转码 | 直接将源视频和音频转为 H.264/AAC、48 kHz、faststart MP4。 |
| 3 参数 | `worker/media.py` 中固化编码参数，通过 `I2V_FFMPEG` 指定二进制。 |
| 4 FFprobe | 当前调用链无 FFprobe。 |
| 5 成片验收 | FFmpeg 退出成功且文件大小在 30 MB 内即接受。 |
| 6 时长 | 期望秒数用于估算码率和写入 Work，没有检查视频实际时长。提交时从所选引擎写入 Work.duration_sec。 |
| 7 音轨 | `-map 0:a:0` 强制第一条真实音轨，无音轨会失败。 |
| 8 编码 | 仅通过写入参数指定编码，未检查最终包和流。 |
| 9 完整性 | 出件后进行对象长度、SHA-256 比对，未做媒体完整解码。 |
| 10 上传 | `pipeline.upload_and_finish` 读取整片后经 `artifacts.upload` 调用存储 SDK。 |
| 11 上传失败 | 仍保留 uploading 和原 MiniMax ID，恢复时从对象检查点或上游继续。 |
| 12 写库失败 | 以预先持久化的 Key/大小/摘要验证对象并补写，旧检查点当前仅证明对象字节一致。 |
| 13 进程死掉 | 正常 subprocess 超时可终止直接子进程，Worker 硬杀后 FFmpeg 无父进程保护。 |
| 14 临时空间 | 使用 TemporaryDirectory 普通退出清理，硬杀残留无安全扫描。 |
| 15 旧租约 | 成功回写验证 owner、generation、租期与 Work 状态。转码期间不读取心跳丢失事件。 |

证据 [媒体](../../../backend/worker/media.py) · [流水线](../../../backend/worker/pipeline.py) · [对象](../../../backend/worker/artifacts.py) · [回写](../../../backend/app/services/handoff_sync.py) · [租约](../../../backend/worker/execution.py) · [生成提交](../../../backend/app/services/works.py)。

部署基线有 WSL Docker；Windows 已有 FFmpeg，测试补齐匹配版本 FFmpeg/FFprobe。当前演示进程、数据库及真实 MiniMax 凭据独立于本轮测试。
