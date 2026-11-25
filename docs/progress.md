# 开发进度

> 由 Claude 在每个阶段结束时更新。新会话开始时先读本文件。

| 阶段 | 范围 | 状态 |
|---|---|---|
| Phase 1 | 底座 + D1（认证、考生管理、题目管理） | 已完成（2026-11-23） |
| Phase 2 | M2：双端隔离认证、Excel 1000 行极速导入、题目与配额联动 | 已完成（2026-11-23） |
| Phase 3 | M3：开放时段控制、配额精细调控、防超发提交网关 | 已完成（2026-11-23） |
| Phase 4 | **M4：安全下载与断点续传、模型管理台、5 维统计、操作审计** | **已完成（2026-11-23，pytest 38 passed / 73.0s）—— 后端 API 封版** |
| Phase 5 | **M5：Vue3 前端底座 + 考生端全流程闭环 + Mock 实体成片** | **已完成（2026-11-23，构建通过 / 端到端冒烟全绿）** |
| Phase 6 | **M6：管理后台前端 + 测试库隔离 + Nginx 网关 + 100 并发压测** | **已完成（2026-11-23，pytest 38 passed / 压测全绿）** |
| Phase 6.2 | **M6.2：单题极简模式（屏蔽题目管理 + 考生创作居中 + 画幅自适应首帧）** | **已完成（2026-11-24，38 passed / 8080 端到端冒烟全绿）** |
| Phase 6.3 | **M6.3：单题居中极简模式 + 世赛中文官网视觉语言复刻（精确视觉版）** | **已完成（2026-11-25，构建零错 / 38 passed / 8080 冒烟全绿）** |
| Phase 6.3.1 | **M6.3.1 终极封版：移除工作台示例词 + 密码零例外加固** | **已完成（2026-11-25，39 passed / 8080 冒烟全绿）** |

## 当前状态

**后端 API 已封版**：M1 底座 + D1 + M2 + M3 + M4 全部完成，`pytest -v -s` 共 **38 项全绿**（73 秒）。

### M4 新增能力

| 分组 | 接口 | 要点 |
|---|---|---|
| 作品列表 | `GET /api/me/works` | 按题目分组 + 平铺列表；含状态、提示词、引擎、错误信息、30 分钟预览签名链接、`download_ready` |
| 安全下载 | `GET /api/works/{id}/download` | 归属 + `succeeded` + 对象存在三重校验；**1800 秒**签名；`ResponseContentDisposition` 强制另存为 `{账号}_{题号}_第{seq}次_{时间戳}.mp4`（RFC 5987 中文兼容）；默认返回 JSON，`?redirect=1` 返回 302 |
| 打包下载 | `GET /api/me/download-all` | `zipstream-ng` + `ZIP_STORED` 流式边读边吐，内存恒定；同名冲突自动 `(2)`；无作品 400 |
| 模型管理 | `GET /admin/engines`、`PUT /admin/engines/{id}`、`POST /admin/engines/{id}/set-default` | 健康度 Redis 优先 / DB 1 小时兜底，失败率 >20% 标红；编辑同步 `i2v:engine:config:{code}` + `invalidate_engine_cache()` + 审计；**默认引擎不可直接停用（400）**；一键切 480P 后新提交立即生效 |
| 用量统计 | `/admin/stats/{overview,by-candidate,by-question,by-engine,by-day}` | 5 维度全部与真实表逐笔对账；费用 = `SUM(duration_sec × price_per_sec)` |
| 报表导出 | `/admin/stats/export-csv`、`/admin/stats/export-video-links` | CSV 带 UTF-8 BOM（Excel 直开不乱码）；成片清单给规范文件名 + 30 分钟签名直链（CSV/JSON） |
| 操作审计 | `GET /admin/audit-logs` | 按操作人/动作/对象/时间范围过滤，`created_at DESC, id DESC`，含 before/after JSON |

### M5 新增：考生端前端（`frontend/`）

| 模块 | 文件 | 说明 |
|---|---|---|
| 工程底座 | `vite.config.ts`、`tsconfig.json`、`package.json`、`.npmrc` | Vite5 + Vue3 + TS + Pinia + Router + Element Plus；dev server 固定 `127.0.0.1:5173`，`/api`、`/admin`、`/healthz` 代理到 `127.0.0.1:8000`；npm 缓存固定在 `E:\npm-cache`（不写 C 盘） |
| 请求层 | `src/api/http.ts`、`src/api/candidate.ts`、`src/api/types.ts` | Axios 统一拦截：401 清态跳登录、403/409/410/422/429 分类提示；接口全量封装，无假数据 |
| 状态 | `src/stores/auth.ts` | 考生信息 + 题目列表 + 总配额 + 时段快照 |
| 路由 | `src/router/index.ts` | `/login`、`/workspace`、`/my-works`；守卫：未登录访问受保护页回登录并记 redirect |
| 布局 | `src/layouts/CandidateLayout.vue` | 顶部：系统名、总剩余次数徽章、时段徽章（时段内绿色倒计时每秒递减 / 时段外橙色 + 下次开放时间）、姓名考号、退出 |
| 登录 | `src/views/candidate/LoginView.vue` | 考号密码登录；401 显示剩余尝试次数、429 锁定倒计时、403 账号禁用 |
| 工作台 | `src/views/candidate/WorkspaceView.vue` | 左题目列表（剩余 N/M、已调整徽标）· 中题目详情（参考图放大、3 条示例点击填入）· 右创作区（拖拽上传 + Canvas 压缩 + 提示词 0/300 + 草稿自动保存恢复 + 提交按钮双态置灰）；提交后任务卡每 3 秒轮询，queued/running/succeeded/failed 四态展示，成功内嵌 `<video>` 与下载按钮 |
| 我的作品 | `src/views/candidate/MyWorksView.vue` | 按题目分组、状态标签、在线预览弹窗、单条签名下载、一键打包 zip |
| 工具 | `src/utils/image.ts`、`draft.ts`、`format.ts` | Canvas 长边 ≤1920 压缩（含原图尺寸/压缩后尺寸回显）、localStorage 草稿（含 5MB 降级）、时间与体积格式化 |

**Mock 出片改造**：`services/handoff.py` 成功分支把 `app/assets/mock_sample.mp4` 真实上传到 `i2v-videos` 的 `videos/mock/{task_no}.mp4` 后再回调契约 B，前端可真实播放与下载（`handoff_sync.py` 同步对齐）。

**端到端冒烟**（`scripts/e2e_smoke.py`，全部经 5173 代理）：healthz → 管理员登录 → 建考生/题目/时段 → 考生登录（cand_sid）→ `/api/me` → `/api/questions` → 上传建草稿 → 提交 → 轮询出片 → 预览 Range 206/1024 → `/api/me/works` 预览链 → 签名下载（规范名 1800s）→ 打包 zip 校验，全绿。

### M6 新增：管理后台 + 测试库隔离 + Nginx 网关 + 百人压测

**测试库物理隔离**：`backend/tests/conftest.py` 在导入 app 之前把 `MYSQL_DATABASE` 改成 `i2v_platform_test`、`REDIS_DB` 改成 `15`，启动时用 root 建库授权并同步 `docs/schema.sql`。跑 pytest 只清测试库与 db=15，主库 `i2v_platform` 与 db=0 完全不受影响（实测：跑完 38 项测试后 `DEMO0001` 仍在）。

**管理后台（`frontend/src/views/admin/*`，路由前缀 `/console`）**

| 页面 | 路由 | 关键能力 |
|---|---|---|
| 登录 | `/console/login` | 管理员账号密码，401/429 提示 |
| 考生管理 | `/console/candidates` | 模板下载 / Excel 导入（逐行错误表格）/ 新增 / 导出 xlsx·csv / 打印密码条 / 搜索 / 状态筛选 / 多选批量重置密码·启禁用·加减配额 / 查看初始密码（410 提示） |
| 打印密码条 | `/console/candidates/print-slips` | A4 双列虚线裁剪卡片 + `@media print` 隐藏工具栏，`window.print()` 直接打印或存 PDF |
| 题目管理 | `/console/questions` | 列表（参考图缩略图）/ 新增编辑抽屉 / 最多 3 条示例提示词 / 参考图上传 / 启停 / 删除遇 409 引导改停用 |
| 配额中心 | `/console/quotas` | 按题批量、按考生逐题、多人批量加减；**下调触发 409 → 二次确认弹窗（强制填写原因）→ 带 force_confirm 重提** |
| 开放时段 | `/console/time-windows` | 顶部生效横幅 + 倒计时；CRUD；临时延长（分钟/指定时间 + 二次确认 + 备注） |
| 模型引擎 | `/console/engines` | 健康度（Redis 优先/DB 兜底，异常标红）；改单价/并发/启停；**应急降级开关：一键切 480P / 切回 768P** |
| 用量统计 | `/console/stats` | 6 张总览卡 + 按考生/题目/模型/天 四个 Tab + 导出统计 CSV + 成片下载清单 |
| 操作日志 | `/console/logs` | 操作人/动作/时间范围检索，展开查看 before → after JSON 对比 |

**Nginx 网关**（`nginx/nginx.conf` + compose `nginx` 服务，`127.0.0.1:8080`）：托管 `frontend/dist`（`try_files $uri $uri/ /index.html`），`/api/`、`/admin/`、`/healthz` 反代 `host.docker.internal:8000`，`client_max_body_size 15m`，`limit_req` 20 r/s（burst 40）。

**百人并发压测**（`scripts/load_test_100.py`，4 客户端进程 × 25 并发）

| 场景 | 实测 |
|---|---|
| 100 并发登录 | P50 1137ms / P95 1395ms（bcrypt cost=10，CPU 密集，仅记录） |
| 100 并发建草稿 | P50 504ms / P95 632ms，100% 成功 |
| 100 并发提交生成 | **P50 566ms / P95 918ms**，成功率 100%，0 个 5xx |
| 配额扣减 | `SUM(quota_used) = 100`（严格等于并发数，无超发） |
| 100 并发签名下载链接 | P50 359ms / P95 513ms |
| 100 并发 Range 首包 | **P50 428ms / P95 494ms**，206 成功率 100% |

### M6.2 新增：单题极简模式（全场固定 1 道创作任务）

**后端兜底（不动表结构）**：`SINGLE_QUESTION_MODE=True`（`app/core/config.py`）时，`main.py` 的 `ensure_default_question()` 在「没有任何启用题目」的情况下自动补 `Q1 / 图生视频创作`（3 条运镜示例、默认 2 次），并对全体考生补建配额行；同 code 的停用题会重新启用。多 worker 并发靠 `questions.code` 唯一键 + `IntegrityError` 回滚兜底。pytest 在 `conftest.py` 里把开关置 false，**38 项断言不受影响**。

**考生端**（`views/candidate/WorkspaceView.vue`）：删除左侧题目栏，`max-width:1000px` 居中单卡片；`questions[0]` 静默绑定；上传（保留 Canvas 长边 1920 压缩）+ 提示词（300 字 + 草稿自动保存恢复，**已去掉示例词条**）+ 提交按钮 + `剩余 N / 共 M 次`；下卡片展示排队/生成中进度与 `<video>` 预览下载；文案统一为「生成次数已用完」。**画幅不再手选**，按首帧宽高比用 log 距离就近匹配后端允许的 5 种，偏差 >15% 时提示「成片会裁切或补边」。`MyWorksView.vue` 单题时不再渲染题目标题栏，直接平铺。

**管理端**：侧栏隐藏「题目管理」，`/console/questions` 由 `beforeEnter` 重定向到 `/console/candidates`（`QuestionsView.vue` 源码保留）；配额页锁定唯一启用题、按考生查询只拉该题配额行；统计去掉「按题目」Tab，保留 6 张总览卡 + 按考生/按模型/按天 + 2 个导出。

**演示数据**：`scripts/seed_demo.py` 重写为单题收敛（清压测残留 → 只留 `Q1` → 其余题目停用 → `DEMO0001` 次数锁 2 → 补开放时段 → 自检）；`scripts/e2e_smoke.py` 改为复用唯一启用题目。

**验收**：`vue-tsc --noEmit && vite build` 零错误；`pytest` 38 passed；经 `http://127.0.0.1:8080` 端到端冒烟 17 步全绿（含 1 题、剩余 2 次、出片、Range 206、签名下载、zip 打包）；5 种画幅经网关实测均 200 回显，`21:9` 仍 400 拒绝。

### M6.3 新增：世赛官网视觉语言复刻（精确视觉版）

**视觉基座**（`frontend/src/styles/main.css`）：色板变量 `--i2v-navy #062B56` / `--i2v-navy-2 #0A3D73` / `--i2v-primary #0085A1`（hover `#006D85`）/ `--i2v-cyan #22B8CF` / `--i2v-amber #F5A623`（仅魔方高光与短横线点缀，≤5%）/ 画布 `#FFFFFF`·`#EEF1F5`·`#E5E9F0`；4~6px 微圆角 + `1px solid #DCE3EA` + `0 8px 24px rgba(6,43,86,.07)`；系统字体栈 `"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Segoe UI"`；覆盖 `--el-color-primary` 等 Element Plus 主题变量，全站组件统一换装。

**原创几何组件**：`frontend/src/components/GeoCubeDecor.vue` —— 等轴测立体方块簇（顶面+左面+右面 3 个 polygon，四色 `#08335E`/`#0077B6`/`#1CA7B8`/`#F5A623`），`hero` / `footer` / `login` 三变体，分别用于 Hero 右下、页脚左下、登录页右下、管理登录页。**未使用任何官方会徽、政府署名或实景摄影**，放射网格与六边形徽标同样为内联 SVG/CSS 自绘，无外链字体图片。

**考生端**：`CandidateLayout.vue` 双层深蓝顶栏（上方状态条：开放时段倒计时 + 考号姓名 + 退出；主导航层：六边形徽标 + 居中导航亮青下划线 + 剩余机会 N/M）、200px Hero（放射网格线 + 右下魔方簇）、深蓝页脚（左下魔方 + 技术规格/比赛纪律 + 巨幅水印）。`WorkspaceView.vue` X 型斜切灰白几何背景 + `AI VIDEO STUDIO` 极淡巨幅水印（108px / 0.04 / letter-spacing 12px）+ 居中主标题组 + 1040px 双步骤创作舱（3px 青蓝琥珀渐变顶边、01 首帧视觉素材 / 02 镜头动态提示词、画幅自适应标签、本地草稿提示、3 枚浅青示例词、孔雀青大号提交键）+ 成片展映区（深蓝排队状态条 3 秒轮询 / 16:9 影院黑底播放器 + 规格条 + 下载键）。`LoginView.vue` 全屏深海蓝放射网格 + 大魔方 + 左主视觉/右白色登录卡；`MyWorksView.vue` 规整画廊卡片。

**管理端**：`AdminLayout.vue` 深海蓝渐变侧栏 + 六边形徽标 + 孔雀青激活项；`AdminLoginView.vue` 深蓝网格 + 魔方装饰 + 硬朗白卡。

**验收**：`vue-tsc --noEmit && vite build` 零错误（1722 modules）；经 8080 冒烟 17 步全绿；38 passed；主库单题 `Q1 图生视频创意作品制作`（`Q2` 停用）、`DEMO0001` 次数 2/0、`works=0`；离线自包含核验（无外链字体/图片）；headless Chrome 实拍验证渲染。

### M6.3.1 封版微调

- **工作台**：`WorkspaceView.vue` 删除 `.examples` 快捷示例词胶囊、`examples` computed、`applyExample()` 与相关样式；`02 镜头动态提示词` 区只保留输入框、300 字实时计数与本地草稿自动保存恢复提示。
- **密码零例外**：核查并锁定 4 个密码产生点（Excel 导入 `candidates.py:295` / 单条新增 `candidates.py:573` / 单人重置 `candidates.py:732` / 批量重置 `candidates.py:770`），全部唯一调用 `generate_exam_password()`；`CandidateCreateIn` 移除自定义 `password` 字段与旁路，请求体里即便塞弱密码也会被忽略。
- **回归护栏**：`backend/tests/test_d1.py` 新增 `test_create_candidate_password_is_compliant`（5 次单条建号断言 8 位 + 大小写 + 数字 + 无 `O0Il1`，并断言弱密码参数被忽略）；`scripts/e2e_smoke.py` 增加返回初始密码合规断言，输出行标注「合规：无易混字符·含大小写+数字」。
- **验收**：`vue-tsc --noEmit && vite build` 零错误（`index-Cc36fVCw.js`，dist 无残留旧 bundle）；`pytest` **39 passed**；经 8080 冒烟全绿（`初始密码 am3Bncfa`）；主库 `DEMO0001` 次数 2/0、`works=0`、启用题目 1 道。

### 核心验收实测（本机）

| 场景 | 结果 |
|---|---|
| Range 断点续传 | 真实 HTTP GET 带 `Range: bytes=0-1023` → **206**，响应 1024 字节，`content-range: bytes 0-1023/8192` |
| 越权下载 | 考生 B 访问考生 A 的作品 → 403/404 |
| 未出片 / 对象缺失 | 409 `WORK_NOT_READY` / 404 `VIDEO_OBJECT_MISSING` |
| 流式 zip | 2 个成片打成 `{账号}_全部作品_{日期}.zip`，解压后字节数与入桶一致（5000 / 7000） |
| 480P 一键降级 | 切默认后新提交 `works.engine_code == 'wq-v1-lite'`；停用默认引擎被 400 拦截 |
| 统计对账 | 2 考生/2 题/3 成功+1 失败：总数 4、成功率 75%、时长 45s、费用 18.0000 元，各维度逐项吻合 |
| CSV | 以 `\xef\xbb\xbf` 开头，表头与行数正确 |

### 历史阶段口径变更（保留）

| 项 | 旧口径 | 新口径（M2/M3/M4） |
|---|---|---|
| 会话键 | `i2v:sess:*` | `i2v:session:{role}:{sid}` + `i2v:active_sid:{role}:{uid}`，sid 32 字节 hex |
| 登录锁定 | 423 | **429** |
| 自动考号 | `stu0001` | `C2026` + 6 位序号 |
| 题目删除 | 一律软删除 | 默认物理删除；有作品 409；`?soft=true` 才软删除 |
| 初始密码可查看期 | 72h | **168h（7 天）** |
| 初始密码过期查询 | 200 + null | **410 Gone** |
| 批量哈希 | ThreadPool(16) | **ProcessPool(14, spawn, 预热)**，线程池兜底 |
| 配额扣减 | —— | 单条原子 `UPDATE ... WHERE quota_used < quota_total`（M3） |
| 成片下载 | —— | 1800s 签名 + 规范文件名 + Range 支持（M4） |

### 测试结果

```
cd backend && .\.venv\Scripts\pytest.exe -v -s
38 passed in 73.04s
```

- `tests/test_m1_foundation.py`（4）：健康检查、密码与加解密、Mock 闭环、并发退还幂等
- `tests/test_d1.py`（13）：导入/行号错误/1000 行 ≤10s/锁定/互踢/双端隔离/配额补建/契约 C/导出/参考图/重置密码
- `tests/test_m2_auth_users_questions.py`（8）：双端隔离、互踢+锁定、1,005 行导入、新增题目 6→8、踢下线、审计、删除保护、410
- `tests/test_m3_quotas_windows_gateway.py`（7）：时段拦截/到期、临时延长、配额调整与下调保护、**20 并发不超发**、幂等、失败退还
- `tests/test_m4_download_engines_stats.py`（6）：**206 断点续传**、流式 zip、480P 一键降级、5 维对账、CSV BOM 与成片清单、审计检索

## 如何启动与测试

```powershell
# 0) Docker CLI 不在 PATH，用全路径（或自行加入 PATH）
$docker = "E:\Docker\DockerDesktop\resources\bin\docker.exe"

# 1) 起基础设施（项目根目录）
cd E:\DeepSeekHarness\frame\i2v-platform\i2v-platform
& $docker compose up -d mysql redis minio
& $docker compose ps          # mysql / redis / minio 均应为 healthy

# 2) 后端
cd backend
.\.venv\Scripts\pytest.exe -v            # 38 passed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3) 前端（另开一个终端）
cd frontend
npm install                              # 首次；依赖与 npm 缓存都在 E 盘
npm run dev                              # http://127.0.0.1:5173（开发）
npm run build                            # 产物 dist/，供 Nginx 托管

# 4) Nginx 生产网关（托管 dist + 反代后端）
cd ..
& $docker compose up -d nginx            # http://127.0.0.1:8080

# 5) 演示数据 + 端到端冒烟 + 压测（可选）
backend\.venv\Scripts\python.exe scripts\seed_demo.py     # 2 题 + 演示考生 + 4 小时时段
backend\.venv\Scripts\python.exe scripts\e2e_smoke.py     # 经 5173 代理跑全链路
backend\.venv\Scripts\python.exe scripts\load_test_100.py # 100 并发压测（跑完自动清理）
```

**入口与账号**

| 入口 | 地址 | 账号 |
|---|---|---|
| 考生端（Nginx） | <http://127.0.0.1:8080> | `DEMO0001` / `6p7sq8ZY` |
| 管理后台（Nginx） | <http://127.0.0.1:8080/console/login> | `admin` / `Admin@2026!` |
| 考生端（Vite 开发） | <http://127.0.0.1:5173> | 同上 |
| 后端接口文档 | <http://127.0.0.1:8000/docs> | — |

**演示账号（当前库内）**：考号 `DEMO0001` / 密码 `6p7sq8ZY`（重置密码后以 `scripts/seed_demo.py` 输出为准）。
管理员：`admin` / `Admin@2026!`（`http://127.0.0.1:8000/docs`）。

## 已知问题

1. **Docker CLI 未加入系统 PATH**：`E:\Docker\DockerDesktop\resources\bin\docker.exe`。
2. **MinIO 官方镜像已下架**，用社区同源重建镜像 `alpine/minio:RELEASE.2025-10-15T17-29-55Z` + `minio-init` 修属主。
3. **本机 `NO_PROXY` 含 `[::1]`**：httpx 需 `trust_env=False` 才能直连 `127.0.0.1:9000`（M4 的 Range 实测已按此处理）。
4. **Mock Worker 只写记录不写文件**：`videos/mock/{id}.mp4` 在 MinIO 中并不存在，下载会返回 404 `VIDEO_OBJECT_MISSING`（这是设计如此，真实 Worker 产出后即可下载）。
5. **管理端登录锁定计数在 Redis**（5 分钟 TTL）。
6. **哈希进程池** `HASH_POOL_WORKERS=14`；进程池异常自动降级线程池，`BCRYPT_ROUNDS` 恒为 10。
7. **测试收尾等 Mock 协程**（`handoff.wait_mock_tasks`），全量用例约 73 秒。
8. **`engines.price_per_sec` 默认 0**：M4 统计测试会把 `wq-v1`/`wq-v1-lite` 单价临时设为 0.5 / 0.2 元/秒来验证折算，用例跑完保留该值（无其它用例依赖单价）；本次交付后已手工重置为 0.0000，需要真实费用统计时在后台填单价即可。
9. 未实现：Vue 3 双端前端（Phase 5）。
