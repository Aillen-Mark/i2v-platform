# 007 Worker 恢复审计

现有任务使用晚确认、进程丢失后拒绝并重发、prefetch 1、600/645 秒软硬超时与 3600 秒 broker 可见性。Redis Work 锁为 690 秒。扫描在生成队列中，主要按 queued 两小时和 running 约 690 秒的更新时间推定异常。既有回写只检查 Work 状态，没有执行世代，因此无法阻止过期任务继续写。[任务](../../../backend/worker/tasks.py) [状态](../../../backend/worker/state.py) [回调](../../../backend/app/services/handoff_sync.py)

收件、排队、领取和执行分开。重发可能发生；按 MySQL 执行租约与持久尝试决定是否可操作。旧任务有可信上游 ID 时续查，无 ID 且可能已投递的任务保守隔离。历史候选人关联缺失时使用 Work ID 关联并单独报告缺少配额的异常。
