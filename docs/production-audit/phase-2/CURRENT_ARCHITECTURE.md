# 当前架构

基线 `62f641f`，工作树干净，tree `05964f608fd5a48a3a72436d31fa67fd4699538e`。FastAPI 创建草稿时先写对象存储；提交生成时锁定 Work，条件更新配额，写扣额审计并创建以 Work ID 为主键的 Outbox。同一事务失败会回滚这些写入。两次独立创建草稿仍可产生两个 Work，本阶段保持该 API 契约。消费者不执行扣额。[提交](../../../backend/app/services/works.py) [Outbox](../../../backend/app/services/generation_outbox.py)

Dispatcher 使用 MySQL 领取与发布租约，Celery 消息经 Redis 传递。新 Worker 首次领到消息时写收件时间，但收件与开始执行间可能中断。Redis 承担旧 Worker 锁、开始时间、并发槽、尝试标记与 UI 排队。MySQL 保存 Work、配额、审计、Outbox 与已回写的上游 ID。MinIO 承载首帧和完成文件。[派发](../../../backend/worker/outbox_dispatcher.py) [Worker](../../../backend/worker/tasks.py)
