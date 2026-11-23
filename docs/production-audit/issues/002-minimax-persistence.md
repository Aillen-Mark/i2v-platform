# 002 持久化 MiniMax 提交尝试与未知结果

**优先级** P0
**审查结论** 已确认，静态审查及现有 Worker 单测支持。单次超时不立即盲重发已有覆盖。
**发布状态** 待批准

## 问题背景

本地 Work 只持有当前上游任务号。Create POST 发出与收到上游任务号之间存在不可原子提交的外部副作用窗口。

## 实际代码依据

`create_upstream()` 将 `pending` 和返回的 ID 暂存 Redis，随后写 MySQL。`MiniMax._request()` 对连接失败、明确限流、读失败和查询故障分类，读超时当前走无自动重投的失败退额。现有适配器没有供应商幂等键。[提交](../../../backend/worker/pipeline.py#L47) · [错误分类](../../../backend/worker/minimax.py#L33) · [MiniMax 创建规范](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create) · [上游查询规范](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-query)

## 风险说明

Worker 在收到 ID 后未写入 MySQL就退出，恢复依赖缓存。Redis 丢失时可能丢关联并再次生成。上游已收费时，直接失败退额可能丢失可交付结果。

## 改造目标

将每次创建命令、结果分类、上游 ID 和可用请求标识持久化。未知请求进入受控对账，已知上游 ID 继续查询。

## 涉及文件

`backend/worker/minimax.py`、`pipeline.py`、`state.py`、待审查的尝试模型和迁移、Worker 与集成用例。

## 修改范围

引入明确未提交、明确已提交、未知三类状态，记录请求/响应时间和净化错误。POST 前先保存尝试并用唯一约束防并行。调研官方可核实的幂等键或可关联回调。若供应商无法从本地尝试标识反查，保留告警和人工对账状态，禁止依据时间或提示词猜测关联。

## 明确不修改的内容

不更换模型、不扩容供应商账户、不改用户提示词或输出规格；成片验真属 008/009。

## 数据库影响

新增 generation_attempts 与唯一键、到期查询索引。既有 `works.upstream_task_id` 可作为兼容读取，禁止凭推测回填历史尝试或供应商账单。

## 兼容性要求

外部 API 响应字段保持可读，未知结果映射为现有进行中展示或经批准扩展。错误与日志去除签名 URL、Bearer 和原始请求体。

## 测试要求

供应商协议替身精确注入接单后丢响应、429、5xx、POST 结果未知和查询失败。另用独立限额账户验证已知 ID 查询和真实生成，预算先审批。

## 验收标准

- [ ] 重启或清 Redis 后已知 ID 可以继续查询。
- [ ] 对结果未知的 Work 不自动 POST 第二次，有可查询、有时限的对账记录。
- [ ] 可证明未提交时才按批准策略申请重试，结算幂等。

## 回滚方案

先停止新增派发并盘点未知尝试；保留尝试表和已知上游 ID。旧 Worker 无法识别未知态，回退前必须隔离这批任务，禁止直接重投。

## 依赖的其他 Issue

[001](001-transactional-outbox.md) 的事件身份与执行交接。
