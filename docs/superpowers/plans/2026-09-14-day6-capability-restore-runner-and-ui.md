# Day 6：能力门修复、Runner 恢复演练与关键 UI/SSE 实施计划

> **For agentic workers:** 本项目规则**禁止 subagent**（CLAUDE.md §13 / HANDOFF 坑 35），执行方式为主会话内联逐任务执行；核心代码由用户手敲、助手按 TDD 红绿循环协助评审（v0.3 §25 学习边界——UI/前端不在 §25 亲手清单内，可由助手实现）。步骤使用 checkbox（`- [ ]`）语法跟踪。

**Goal:** 三条线收束 Day 5 Pilot 暴露的两个 FAIL 门与 Day 6 规格 DoD——① **能力门**：提交语义对齐排查（零付费）→ c/a 策略修复 → 小样本验证（付费 Gate）；② **预算门**：Full 范围/成本结构重设计方案对比（交用户裁定）；③ **Day 6 规格 DoD**：Runner 人为中断恢复 E2E + 关键 UI/SSE/安全 E2E（按 §22 风险表收窄）。实现任务零付费，付费项显式授权。

**Architecture:** 复用 Day 4 产品三闭环（已稳定）与 Day 5 评测栈（BirdSystemServerAdapter、EvaluationRunner、eval schema）；新增四处接缝——`SpoolImporter`（宿主侧归集 spool → `eval.task_attempt.telemetry` 回填，join 键 = 官方 `session_id`）、`src/commerce_agent/api/`（最小 FastAPI + SSE 稳定事件面）、`web/`（React + TypeScript + ECharts，工作台 + 审批关键路径 + 评测中心只读）、`tests/e2e/`（API 层 E2E）。不引入 Redis/Celery/OTel/Langfuse/Kubernetes（v0.3 §21）。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、psycopg、pytest、React、TypeScript、ECharts、Docker Compose。

**Spec:** `docs/project/specs/BIRD-Interact电商经营分析Agent-设计规格-v0.3.md` §18（Trace 与 spool 归档）、§19.3（协议与端到端测试）、§20（UI 范围）、§21（技术栈与目录）、§22 风险表（「UI 挤压核心 → 只实现工作台与审批关键路径」——BIRD 侧 rewards 全 0，本条款适用）、§22 Day 6 里程碑（验收门：主产品链路可演示；Runner 中断恢复通过）；一手材料：`docs/reports/2026-09-13-task13-pilot-report.md`（两门 FAIL 证据与机制）、`docs/reports/2026-09-13-task13-pilot-main-run.md`、`docs/reports/2026-09-12-gate-p-preflight-execution-log.md` §6.5（技术债清单）。

## Global Constraints（每个任务隐含包含）

1. **官方 revision 冻结**（继承 Day 5）：BIRD-Interact 源 `451fe2c…`；任何引用官方源的操作只允许 `_upstream/BIRD-Interact/` 下显式列出的文件（Task 1 排查文件清单见任务体），禁止遍历其他路径；evaluator-only 内容一律不可触。
2. **GT 隔离是硬门**（继承）：GT 只进官方 orchestrator/evaluator 子进程。
3. **实现任务零付费**：Task 1–9、11 零 DeepSeek 调用（FakeModel/Stub/harness 覆盖）。付费项仅两处、各自独立 Gate：Task 3 探针执行（≤$0.01）与 Task 10 小样本验证（2–4 集，≤$0.10，含 sim 侧）。
4. **Gate 分离**：本计划批准 = Phase A/B 零付费实施授权；Git 提交仍需用户逐次授权；付费 Gate（Task 3/10）单独授权；Day 7 产品 50 题与实验另行计划。
5. **冻结契约不可动**：Task 2 的归集 join 只读官方响应既有字段（`init_session` 响应 `session_id`、请求 `task_id/mode`）；不修改官方 DTO、不要求 agent 协议加字段。spool 事件是我方私有轨道，可加字段。
6. **隐私**：SSE 事件与 UI 只显示允许公开的数据和 SQL（§18）；不出现 API key、GT 字段名值、evaluator-only payload、raw seller ID。
7. **学习边界**：`SpoolImporter`、SSE 事件面、Runner 恢复演练的判据代码属核心——用户手敲，助手 TDD 协助；UI 组件与样式助手可实现。
8. **测试纪律**：默认离线 suite 不依赖网络/真实 DB；PG marker 测试默认 skip；E2E 放 `tests/e2e/`；每 Task 完成 owning tests + 全量 suite + Ruff 后才申请 commit。
9. **两门现状基线**：预算门 FAIL（457.6 元 / 160 线）与能力门 FAIL（rewards 全 0）是本计划的输入，不是可协商前提；所有成本重估以 Pilot 实测单价为准（c 集 agent $0.0100、sim $0.03144、a 集 agent p95 $0.0188）。
10. **UI 收窄**（§22 风险表）：只实现分析工作台 + 运营审批关键路径 + 评测中心只读；「知识与数据」界面、Pro/Thinking 开关等非 DoD 项不做。

---

## Phase 划分与授权边界

```text
Phase A（Task 1–9）：零付费——静态排查、spool 归集、技术债、策略修复实现、Runner 演练、SSE/UI/E2E
Phase B（Task 10，需付费 Gate B）：小样本策略验证（2–4 集新 experiment，≤$0.10 含 sim 侧）
Phase C（Task 11，零付费分析）：Full 重设计方案对比 → 用户裁定（Full 启动与否归 Day 7 / 独立裁定）
```

执行顺序建议：Task 1 最先（其结论决定 Task 5 形状）；Task 6 独立可并行；Task 7→8→9 依赖链；Task 10 必须在 Task 5 之后；Task 11 收尾。

---

### Task 1: 提交语义对齐排查（零付费静态，能力门第一优先）

**背景:** Pilot rewards 全 0 的待排查替代假设——agent 的 `submit_sql`（bird_a 官方 9 工具之一，coin_cost=3）与最终答复是否真正进入官方评审通道；a-mode 7/9 集已提交却 0 分，若是通道断裂则属集成缺陷而非 SQL 质量。

**Files:**
- 只读：`_upstream/BIRD-Interact/` 冻结允许清单内 orchestrator/评审路径（实施时精确列出并经用户确认，延续「显式 allowlist」纪律）
- 只读：`outputs/bird-eval/episodes/`（d 实验已提交集的 dialogue_history 终态）
- Create: `docs/project/research/2026-09-14-submit-semantics-alignment.md`

**Steps:**
- [ ] Step 1: 列出排查用官方文件清单（orchestrator 会话终局、submit_sql 工具处理、评审器输入来源），提交用户确认后阅读。
- [ ] Step 2: 回答三个问题并留证据引用——① 官方评审读取什么（submit_sql 动作参数？dialogue_history 最终 agent 消息？state 字段？）；② `Budget exhausted. Task ended.` 与我方预算门 typed 停止（`provider_response_invalid`）两种终局下，官方各拿到什么提交物；③ 提交格式（SQL 裸文本/markdown 包裹/multiple 语句）是否符合评审器期望。
- [ ] Step 3: 用 d 实验 10 次 submit_sql 的 episode 逐一对拍（提交内容 vs 评审期望格式），归类：通道断裂 / 格式失配 / SQL 质量问题，各占几集。
- [ ] Step 4: 产出研究笔记（结论 + 逐集对拍表 + Task 5 修复方向的判定）；若通道断裂，列出最小修复面。

**判据:** 笔记能解释全部 10 次提交 0 分的归属；Task 5 范围由此确定。

---

### Task 2: spool 导入接线（每集成本归属，技术债①）

**背景:** Gate P §6.5 开放项——agent spool 的 `attempt_id` 是会话级 uuid4，与 eval attempt 无法重算同一 digest。实测确认官方 `init_session` 请求含 `task_id/mode`、响应含 `session_id`——join 键存在且不动冻结契约。

**Files:**
- Modify: `bird_system_agent/runtime.py`（`SpoolTraceGateway` 事件增加 `task_id`/`mode` 字段——init_session 入站已知；`session_id` 由 adapter 响应回填）
- Create: `src/commerce_agent/evaluation/spool_importer.py`（`SpoolImporter`：遍历 `BIRD_SPOOL_DIR`，按 `(task_id, mode, session_id)` → eval attempt 归集；主 join = episode 输出/adapter 记录中的官方 session_id，兜底 = (task_id, mode) + started_at 时间窗）
- Modify: `src/commerce_agent/evaluation/` executor 接缝（episode 终结后回填 `eval.task_attempt.telemetry` 的 `agent_cost/agent_usage/turns/cache_hit_ratio`）
- Create: `tests/unit/evaluation/test_spool_importer.py`、`tests/integration/evaluation/test_spool_import_pg.py`（`-m postgres`）

**Steps:**
- [ ] Step 1: 验证 episode 输出 JSON 中官方 `session_id` 可得性（d 实验 episodes 现场核对）；不可得则落兜底时间窗方案并记录残差风险。
- [ ] Step 2: RED——归集测试（合成 spool 事件 + Fake 时刻表）：并发 2 下两集归属正确、跨 experiment 隔离、时间窗歧义 fail-closed（归「未归属」不入账）。
- [ ] Step 3: GREEN 实现 + telemetry 回填（jsonb 原子 UPDATE，failed attempt 也回填实际消耗）。
- [ ] Step 4: §18 归档语义——spool 只读归档（`archive/` 子目录，Day 5 已有骨架）、sequence 单调性与文件大小校验进 importer。
- [ ] Step 5: 对 d 实验 20 attempt 做一次真实回填演练（只读 spool + PG 写 telemetry；属既有数据整理，先获用户授权），与 Pilot 报告聚合数字对账。

**判据:** 回填后 `SELECT mode, avg((telemetry->>'agent_cost')::numeric)` 与报告 §3.2 分层单价一致（±10%）；未归属 turn 计数披露。

---

### Task 3: 能力探针重设计（技术债②，执行需付费 Gate）

**背景:** Day 3 探针门（固定 SQL 链）与 Day 4/5 架构不兼容（Gate P 判 FAIL 保留 + documented limitation）。重设计为验证**当前架构真实调用面**的最小探针。

**Files:**
- Create: `scripts/probe_model_capability.py`（1–2 模型轮固定形状脚本：回显校验、tool-calling 一轮、usage/cost/band 核算闭环、快照 marker 校验）
- Modify: `docs/reports/` 探针门判据文档（新判据替代 Day 3 固定 SQL 链）

**Steps:**
- [ ] Step 1: 判据定稿（零付费）：每项判据对应 Pilot 暴露过的真实风险面——回显名容差（deepseek-flash 更名先例）、工具目录 ⊆ 契约（Task 13 缺陷⑥）、band 判定（周日 off_peak 先例）、成本核算 Decimal 闭环。
- [ ] Step 2: 脚本实现 + 离线测试（FakeModel 覆盖全部判据分支）。
- [ ] Step 3: **付费执行（Gate：≤$0.01）**——单轮最小真实调用，产出探针报告与快照兼容结论。

---

### Task 4: v2 decide 工具名单一致性审计（技术债③）

**背景:** Gate P §6.5 技术债——v2 run-profiles 的 decide 工具名单与图白名单不一致（Task 13 缺陷⑥①修复 bird_a 时未覆盖 decide 轨）。

**Files:**
- Modify: `tests/contract/test_bird_tool_catalog.py`（守卫从 BIRD 目录 ⊆ 契约 actions 扩展为**三方一致性**：每个 run-profile 的规则工具集 ↔ 对应图/端口 allowlist ↔ 冻结契约 fixture，按 profile 查重既有语义保留）
- Modify: 发现的不一致项（`configs/model/run-profiles.v2.json` 或图侧 allowlist，以 Task 1 排查同款「最小修复面」呈现后修复）

**判据:** 三方一致性守卫测试全绿；c/a/decide 各 profile 的工具集与图白名单精确相等。

---

### Task 5: c/a 策略修复（依赖 Task 1 结论，能力门主修复）

**背景:** Pilot 机制证据——c-mode 9 集 `ask_user` 539 次、`submit_sql` 仅 1 次（澄清循环失控）；a-mode 探索挤占提交、已提交 SQL 未过评审。修复全部在 prompt/policy/graph 策略层，不动官方语义与我方状态机。

**Files:**
- Modify: `src/commerce_agent/context_builder/`（bird_a/bird_c profile 的 prompt policy：c-mode 增加澄清预算规则——「至多 N 轮澄清后必须基于已知信息作答并 submit_sql」；a-mode 增加「先提交一版，再用剩余 coin 迭代」策略）
- Modify: `src/commerce_agent/orchestration/`（如需 graph 层停止闸：c-mode ask_user 计数闸——非首次澄清轮直接注入策略提醒；实现对齐官方 before_model_callback 语义，拒绝路径携带原 call_id——坑 57）
- Test: `tests/unit/context_builder/`、`tests/unit/orchestration/`（策略断言 + 离线图测试）

**Steps:**
- [ ] Step 1: RED——c-mode「60 轮全 ask_user」最小复现测试（离线图 + FakeModel 计数 ask_user 调用）。
- [ ] Step 2: GREEN——策略修复；a-mode 提交预留同理红绿。
- [ ] Step 3: 离线端到端演练（FakeModel 模拟「澄清循环」行为模式）确认轮次分布改变：c-mode submit_sql ≥ 1/集。
- [ ] Step 4: 每集成本影响预估更新（c-mode 轮次 60→N 对 agent+sim 成本的敏感度表，供 Task 11 用）。

**判据:** 离线演练中 c-mode 每集至少一次 submit_sql；prompt policy 三轨不交叉测试保持全绿。

---

### Task 6: Runner 人为中断恢复 E2E（Day 6 验收门②，零付费）

**背景:** Day 5 已有状态机 6×6 矩阵与 gather-vs-stop 竞速修复；Day 6 门要求「Runner 中断恢复通过」的端到端演练。伪 orchestrator、PG 真库、零付费。

**Files:**
- Create: `tests/integration/evaluation/test_runner_sigint_recovery.py`（`-m postgres`）

**Steps:**
- [ ] Step 1: RED——SIGINT 演练：4 题任务清单（2 completed + 1 running + 1 pending），运行中发 SIGINT → 断言 stopped checkpoint 落库、running attempt 置 interrupted。
- [ ] Step 2: 重启 Runner 同 experiment → completed 不重跑、interrupted 题新 attempt_seq=2 从头跑、pending 照常。
- [ ] Step 3: 事件 JSONL 与 eval 表状态交叉断言；finally 语义（§6.1 坑 6 同款）核验。

**判据:** 与 §19.3 末条（「Runner 人为中断后恢复，不重复 completed task；中断题产生新 attempt」）逐字对应。

---

### Task 7: SSE 事件面（`src/commerce_agent/api/` 全新）

**Files:**
- Create: `src/commerce_agent/api/__init__.py`、`app.py`（最小 FastAPI 工厂）、`events.py`（稳定事件 schema）、`sse.py`（`/api/runs/{run_id}/events` SSE 端点）
- Create: `tests/unit/api/`（事件 schema、SSE 格式、隐私断言）

**Steps:**
- [ ] Step 1: 稳定事件类型定稿（前端只依赖事件类型，不依赖 LangGraph 节点名——§20）：`clarification_requested / proposal_created / approval_required / approval_decided / execution_started / execution_receipt / sql_rejected / sql_repaired / report_ready / run_interrupted / run_resumed`（以产品三闭环真实事件为准裁剪）。
- [ ] Step 2: 事件源 = 产品 Trace 表（app 轨）轮询/notify，SSE 推送；payload 白名单字段（§18 隐私：无 raw seller ID、无 HMAC/grant、SQL 仅已批准公开的）。
- [ ] Step 3: 断线重连语义（Last-Event-ID 回放窗口）与空状态心跳。

**判据:** SSE 端点在未审批/拒绝/修复/恢复各状态下推送的事件序列与产品闭环审计一致（PG 集成测试）。

---

### Task 8: 关键 UI（`web/` 全新，助手可实现）

**Files:**
- Create: `web/`（Vite + React + TypeScript + ECharts；`web/package.json` lockfile 独立——§21 产品与 BIRD 各自独立 lockfile 精神）
- 页面（§22 收窄）：分析工作台（对话/澄清/计划/证据/SQL/表格/图表/对账/结论）+ 运营审批（proposal/Diff/审批/标注）+ 评测中心只读（运行列表、c/a、P1/P2、费用、轮次——数据源 `eval` 表经只读 API）
- 状态展示（§20 收窄子集）：空状态、澄清、SQL 拒绝、SQL 修复、证据不足、待审批、审批拒绝、运行中断/恢复、完成

**Steps:**
- [ ] Step 1: 模拟数据 Demo 布局确认（§53 先 Demo 再实现；模拟事件流驱动全状态可切换）。
- [ ] Step 2: SSE 客户端（EventSource + 重连）与只读 API 对接。
- [ ] Step 3: 评测中心只读页对接真实 eval 数据（rewards 全 0 的 Pilot 数据如实展示）。

**判据:** 主产品链路（澄清→计划→SQL→审批→执行→读回→报告）在 UI 可完整演示——Day 6 验收门①。

---

### Task 9: 安全/E2E 测试（`tests/e2e/` 全新）

**Files:**
- Create: `tests/e2e/test_product_chain_api.py`（API 层 E2E：工作台链路经 SSE + REST 全程，覆盖 §19.3 产品侧条目——模糊经营问题完成澄清/解析/查询/对账/报告；三闭环各一次审批/执行/读回）
- Create: `tests/e2e/test_ui_acceptance.md`（UI 手工验收清单；浏览器自动化工具（如 Playwright）是否引入由用户裁定——§21 必选清单未含，默认不引入）

**判据:** E2E 全绿（PG 模式）；Day 6 两项验收门均可演示。

---

### Task 10: 小样本策略验证（Phase B，**付费 Gate B：≤$0.10 含 sim 侧**）

**前置:** Task 1 结论 + Task 5 实现完成；用户单独授权（新 experiment id、明确上限、熔断条款）。

**Steps:**
- [ ] 2–4 集新 experiment（c/a 各半，选 Pilot 同库不同题避免记忆污染）；验证：c-mode submit_sql ≥ 1/集、reward 通道是否打通（首分 > 0 即通道证明）、每集 agent+sim 新成本读数。
- [ ] 产出验证报告：策略修复前后对比 + 新单价 → 交 Task 11。

---

### Task 11: Full 重设计方案对比（Phase C，零付费分析，交用户裁定）

**输入:** Task 10 新单价 + Pilot 实测基线（c 集 agent $0.0100/sim $0.03144；a 集 agent p95 $0.0188/sim $0.03144；a-mode Full 剩余单项 210 元已超线）。

**产出:** `docs/project/research/2026-09-xx-full-redesign-options.md`——三方向 × 修复后单价重推 §16.4：

| 方向 | 内容 | 规格约束 |
|---|---|---|
| A 范围压缩 | c-only / 分层抽样 / 减量 Full（如各 200 集） | §17.3 定义 600×2；偏离需用户修规格批准 |
| B sim 降本 | sim 模型选型（更低价模型）| 需对照官方规程确认 sim 模型是否可换（Task 1 排查顺带确认） |
| C 上限修正 | 180 元 ceiling 调整 | 规格冻结值，修改 = 用户显式修规格 |

**判据:** 每方向给出重推后的总账、期望 reward 增益假设（显式标注不确定性）与建议；用户裁定后 Full 启动（若批准）归 Day 7 执行，不属本计划。

---

## 验收门（对齐 §22 Day 6）

- [ ] 主产品链路可演示（Task 7–9）
- [ ] Runner 中断恢复通过（Task 6）
- [ ] 能力门修复已验证（Task 1/5/10）且 Full 重设计方案已呈报（Task 11）
- [ ] 三项技术债关闭（Task 2/3/4）
