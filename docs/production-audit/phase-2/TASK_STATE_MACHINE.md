# 状态机

## 改造前的 Work 状态

```mermaid
stateDiagram-v2
  draft --> queued
  queued --> running: 写入上游 ID
  queued --> failed: 退额
  running --> failed: 失败或等待超时
  running --> succeeded: 上传后回写
```

## 改造后的内部状态

```mermaid
stateDiagram-v2
  queued --> prepared: 写入尝试
  queued --> submission_unknown: 历史任务已派发且无可信 ID
  queued --> accepted: 导入历史可信 ID
  prepared --> submitting: 持久化后允许 POST
  prepared --> failed: 请求前明确失败
  submitting --> accepted: 原子保存上游 ID
  submitting --> submission_unknown: 响应不明或进程退出
  submitting --> failed: 明确拒绝
  submission_unknown --> accepted: 人工核对并绑定 ID
  submission_unknown --> manual_review: 到期报警
  manual_review --> accepted: 人工核对并绑定 ID
  manual_review --> uploading: 核实已成功任务并恢复成片处理
  accepted --> running: 查询确认运行
  accepted --> uploading: 确认上游成功
  running --> uploading: 确认上游成功
  accepted --> failed: 明确失败
  running --> failed: 明确失败
  accepted --> cancelled: 确认取消
  running --> cancelled: 确认取消
  accepted --> manual_review: 状态逾期
  running --> manual_review: 状态逾期
  uploading --> succeeded: 校验对象并落库
  uploading --> manual_review: 成片处理逾期或确认无法继续
```

| 尝试状态 | 再次 POST | 退额 | 恢复动作 |
| --- | --- | --- | --- |
| `prepared` | 首次 POST 前须提交 `submitting` | 需先记录明确失败 | 接管后继续准备 |
| `submitting` | 否 | 否 | 能写入明确结果时正常推进，执行中断则进入未知 |
| `submission_unknown` | 否 | 否 | 按时间进入人工核对，或在已查证归属后补录 ID |
| `accepted`、`running` | 否 | 否 | 查询已有 ID，单片超时后继续恢复，状态长期未推进时人工核对 |
| `uploading` | 否 | 否 | 复用通过 SHA-256 和长度校验的对象，或查询原任务并重新处理 |
| `manual_review` | 否 | 否 | 运营人员核实供应商任务归属和状态 |
| `failed` | 可重试的明确失败且预算未用完时，新建尝试 | 终结失败时退一次 | 先检查重试条件，再决定补偿 |
| `cancelled` | 否 | 确认取消后退一次 | 按明确终态结算 |
| `succeeded` | 否 | 否 | Work 与尝试已原子成功，重复消息退出 |

公开 Work 仍使用 `draft/queued/running/succeeded/failed`。内部上传和人工核对使用对外 `running`。过期租约仅允许新执行世代接管。旧世代无法更新进度、上传检查点、终态或发起退款。
