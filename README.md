# i2v-platform · Claude 执行端启动包

## 目录

| 文件 | 作用 |
|---|---|
| `CLAUDE.md` | Claude 每次自动读取的规约：职责边界、技术栈、核心铁律、工作方式 |
| `docs/requirements.md` | 需求说明 v5.2 原文 |
| `docs/schema.sql` | MySQL 8.0 建表脚本（8 张表） |
| `docs/handoff-contract.md` | 你和同伴的交接契约，**也发给同伴一份** |
| `docs/decisions.md` | 已定决策、待确认事项、Claude 的假设 |
| `docs/progress.md` | 进度记录，Claude 每阶段更新，新会话靠它接上 |
| `prompts/01~05` | 按阶段下发给 Claude 的指令 |

## 怎么开始

1. 把整个文件夹放到你的工作目录，执行 `git init && git add . && git commit -m "init"`。
2. 确认本机装好 Docker Desktop、Python 3.11+、Node 20+。
3. 在该目录打开 Claude Code（终端执行 `claude`）。
4. 把 `prompts/01_base_D1.md` 的全部内容粘贴给它。
5. 等它汇报 pytest 全绿 → 你按汇报里的步骤手动验收 → `git commit`。
6. 执行 `/clear` 清空上下文，再粘贴下一个 prompt。依次 01 → 05。

## 每轮验收重点

| 阶段 | 你要亲自看的 |
|---|---|
| 01 | 打开 http://localhost:8000/docs，导入一份 Excel，试一下同账号两处登录 |
| 02 | 20 并发抢最后 1 次是否「1 成功 19 失败」；Mock 失败后次数是否退回 |
| 03 | 下载的文件名对不对、能不能播放；统计数字能否对上 |
| 04 | 浏览器里走完「管理员建题开时段 → 考生传图出片下载」 |
| 05 | 压测报告是否达到需求 5.5 的指标 |

## 小提示

- Claude 汇报里列的「假设」要看一眼，不同意就直接告诉它改，并更新 `docs/decisions.md`。
- 需求方确认了 `docs/decisions.md` 里的待确认项后，先改这个文件再继续下一阶段。
- 如果它某一步卡住或测试反复不过，让它先说明原因和方案，不要让它直接跳过测试。
