# 002 MiniMax 请求审计

旧代码在 Redis 写 `pending` 后 POST，接着把 ID 写 Redis，再写 MySQL。读超时不会立即重发，但流水线会认定失败并退款；Redis 丢失后缺少可辨认的尝试记录。超时清理对仍在运行的任务也可能退款。[流水线](../../../backend/worker/pipeline.py) [适配器](../../../backend/worker/minimax.py)

MiniMax H3 V2 公开创建文档未给出可验证的客户端幂等键。查询按上游 task ID，列表过滤不能用本地请求 ID 找到对应任务。供应商返回的 request ID 与自建 ID 分别对待。没有可靠关联时暂停处理并告警，由运维使用供应商可核实证据关联；不会凭提示词或接近的时间推定关联。

[创建](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create) · [查询](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-query) · [列表](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-list)
