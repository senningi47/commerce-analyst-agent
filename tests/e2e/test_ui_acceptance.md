# UI 手工验收清单（Day 6 验收门①）

> 目的：主产品链路（澄清→计划→SQL→审批→执行→读回→报告）在 UI 可完整演示。
> 浏览器自动化（Playwright 等）**默认不引入**（§21 必选清单未含；Task 8 Step 2/3 已用内置浏览器实机验证一轮）。
> 是否引入自动化浏览器测试由用户在 Day 7 裁定；本清单为人工执行路径。

## 启动

1. 后端（只读 API，含 SSE）：`uv run --env-file .env python scripts/run_api.py`（默认 `127.0.0.1:8010`，可用 `COMMERCE_AGENT_API_PORT` 覆盖）。
2. 前端：`cd web && npm run dev`（`http://localhost:5173`，vite 已代理 `/api` → 8010）。
3. 注意：8000 端口为 PowerContext 服务，勿占用；vite 只绑 `localhost`（IPv6），不要用 `127.0.0.1:5173` 访问。

## 一、演示模式（完整富布局，模拟事件流）

| # | 步骤 | 预期 |
|---|------|------|
| 1 | 打开首页 | 默认演示模式；顶栏「模拟数据 · 15/15 事件」；完成态工作台（结论/三指标卡/图表/提案条/折叠区/审计栏） |
| 2 | 点「自动播放」 | 从空状态顺序推进至完成，每 700ms 一步 |
| 3 | 逐个点选状态条：空状态/澄清/SQL 拒绝/SQL 修复/证据不足/待审批/审批拒绝/运行中断/运行恢复/完成 | 每态渲染对应内容；「SQL 拒绝」展示第一版 SQL + 拒绝原因；「证据不足」为警示结论卡 |
| 4 | 工作台提案条「去审批 →」 | 跳转运营审批页 |
| 5 | 审批页：驳回不填标注 | 「驳回」按钮禁用（标注必填门控） |
| 6 | 审批页：填标注后驳回可用 | 决策依据/Diff（删除线红/新增绿）/预算影响/HMAC 注记可见 |
| 7 | 评测中心（未起后端时） | 显示「评测 API 未连接——以下为演示快照」+ Pilot 口径统计卡 |

## 二、实时模式（真实事件流，需后端）

| # | 步骤 | 预期 |
|---|------|------|
| 8 | 顶栏切「实时」 | 提问栏替换为运行选择器；顶栏「实时 · 未选择运行」 |
| 9 | 点「刷新运行」 | `/api/runs` 返回的运行出现在下拉框（`<短id>… · N 事件 · 时间`）；后端未起时下拉框显示「无法连接运行目录 API」 |
| 10 | 选择一个运行 | 顶栏变「实时 · 已连接 · N 事件」；事件按 cursor 顺序投影：结论/计划步骤/SQL 指纹（无 SQL 文本，§18 摘要面）/对账/提案 ref/审计轨 |
| 11 | 重启后端（杀掉再 `scripts/run_api.py`） | EventSource 自动重连（Last-Event-ID）；事件数不丢不重 |
| 12 | 右侧审计栏 | 最近 6 条事件 + 「更早事件见审计追溯（N 条）」 |

产生实时运行的方式（验证辅助）：`uv run --env-file .env python scripts/seed_ui_live_run.py` 写入一条演示 run；**验证后必须清理**：
`scripts/seed_ui_live_run.py` 场景为 `day6-ui-live-v1`，用 `PostgresScenarioReset.reset("day6-ui-live-v1", "scenario-reset-v1")` 归零。

## 三、评测中心（真实数据，需后端 + eval 库）

| # | 步骤 | 预期 |
|---|------|------|
| 13 | 打开评测中心 | 统计卡读 `ops_read.eval_experiments` 最新实验（Pilot d：20 集 / 18 成功 / 1 failed · 1 基础设施错误 / reward 0.0 红色 / 费用卡按 spool 回填情况标注） |
| 14 | 查看明细表 | 逐集：题目 / c·a / 状态 / P1·P2 / reward / 轮次 / 提交 / agent·sim 费用 / 错误分类；rewards 全 0 如实展示 |
| 15 | 对照基线注记 | 「预算门 FAIL（457.6 元/160 元线）· 能力门 FAIL（rewards 全 0）」可见 |

## 四、隐私红线（任一模式抽查）

- 页面不出现：raw seller ID、HMAC/grant/nonce、GT 字段名值、API key、LangGraph 节点名。
- SQL 仅以双指纹 + 原因码呈现（SQL 文本只在演示模式的模拟载荷中出现，且明确标注 Demo）。

## 验收门对照

- **验收门①（主产品链路可演示）**：本清单 §一 + §二 全部通过。
- **验收门②（Runner 中断恢复）**：`tests/integration/evaluation/test_runner_sigint_recovery.py`（PG 模式）全绿。
