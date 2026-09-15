# Day 7：c-mode 修复闭环、A4 Full 执行与项目收尾计划

> **For agentic workers:** 本项目规则**禁止 subagent**（CLAUDE.md §13 / HANDOFF 坑 35），执行方式为主会话内联逐任务执行；核心代码由用户手敲、助手按 TDD 红绿循环协助评审（v0.3 §25）；UI/文档/脚本助手可实现。步骤使用 checkbox（`- [ ]`）语法跟踪。

**Goal:** 三线收束项目——① **能力门闭环**：c-mode phase-record 静态核验 → 定性修复 → 镜像 rebuild + 容器实证 → 小样本再验证（付费 Gate C1）；② **A4 Full 执行**（用户 2026-09-15 裁定范围：300/模式分层 + sim 合规排查后启用）：四修复项前置 → 抽样清单 → 分批 off-peak 执行 + 消融（付费 Gate C2）→ 产品 50 题（§17.1）；③ **项目收尾**：最终报告、README/面试材料、全面对账。

**Architecture:** 复用 Day 4 产品三闭环 + Day 5/6 评测栈（EvaluationRunner、BirdSystemServerAdapter、spool_importer、eval 只读 API/UI）不变；新增改动面仅在 c-mode 上下文装配（Task 1/2 定性后确定：adapter 会话状态修复 或 ContextBuilder 每轮注入）与四项 infra 修复（实验身份注入/同题串行/子进程诊断落盘/官方库就绪探测）。不引入新依赖。

**Tech Stack:** Python 3.11、pytest、psycopg、Docker Compose（现状，零新增）。

**Spec:** v0.3 §16.4（动态预算账本，每 25 集重估）、§17.1/§17.2/§17.3（三组实验）、§18（Trace 与归档）；上游裁定输入：`docs/project/research/2026-09-15-full-redesign-options.md`（A4 方案 + §11 锚点精化）、`docs/reports/2026-09-14-task10-strategy-validation.md`、执行日志 §19（c-mode 机制发现）。

## Global Constraints（每个任务隐含包含）

1. **官方 revision 冻结**（继承）：BIRD-Interact 源 `451fe2c…`；官方文件只读显式 allowlist 内路径（Task 1 实施时列清单经用户确认），禁止遍历其他路径；evaluator-only 内容一律不可触。
2. **GT 隔离是硬门**（继承）：GT 只进官方 orchestrator/evaluator 子进程；任务问题（question 文本）属官方 agent 可见面，可进我方上下文装配。
3. **付费 Gate 显式**（两处，各自独立授权）：C1 再验证 ≤$0.05（1–2 集）；C2 A4 Full + 消融（预算按 §16.4 重估 ≤160 元线，启动前重估表呈用户）。产品 50 题如产生模型费用并入 C2 授权口径单独列示。
4. **冻结契约不可动**（继承）：官方 DTO/协议/评测器不改；我方 spool 事件私有轨道可加字段。
5. **运行纪律**（继承+新）：付费运行一律 off-peak（北京工作日 09–12/14–18 为 peak=2×）；**栈起动后必须官方库就绪探测再跑**（坑 67）；agent 侧代码/配置变更后必须镜像 rebuild + 容器内实证（坑 64）；同题双模式必须串行（坑 63）；每完成 25 个 Full 任务重估预算，越线安全暂停（§16.4）。
6. **隐私**（继承）：SSE/UI/日志只显示允许公开的数据与 SQL；无 API key、GT 字段值、evaluator-only payload、raw seller ID；官方子进程诊断输出落盘为 agent 可见内容（非 GT）方可保留。
7. **测试纪律**（继承）：每 Task 红绿 → owning tests → 全量 suite + Ruff → commit（commit 逐项授权）。

---

## Phase 划分与授权边界

```text
Phase A（Task 1–3）：零付费——c-mode 静态核验、修复实现（红绿）、suite+rebuild+实证
Phase B（Task 4）：付费 Gate C1 ≤$0.05——c-mode 再验证 1–2 集
Phase C（Task 5–6）：零付费——sim 合规排查、a-mode SQL 诊断、A4 清单与预算重估、四修复项
Phase D（Task 7–9）：付费 Gate C2——A4 分批执行 + 消融 + 产品 50 题（启动前重估表呈用户）
Phase E（Task 10）：零付费——收尾报告、README/面试材料、全面对账
```

执行顺序：Task 1 → 2 → 3 → 4（能力门闭环）；Task 5/6 可与 1–3 并行穿插；Task 7 必须在 4 PASS 且 6 完成后；Task 8/9 随后；Task 10 收尾。

---

### Task 1: c-mode phase-record 静态核验（零付费，能力门第一优先）

**背景:** 执行日志 §19 实锤——官方 c 模式每轮新建 agent 会话（16 会话×1 轮）、per-turn 上下文 = phase record 且未携带任务问题 → agent 逐轮失忆 → 占位符提交。需对冻结官方源定性：我方 adapter 会话状态复现缺陷，还是官方本义（若本义如此则需我方跨轮记忆策略）。

**Files:**
- 只读：`_upstream/BIRD-Interact/` 冻结允许清单内 c-interact 会话/phase-record 相关文件（实施时精确列出经用户确认——延续「显式 allowlist」纪律，参照 Task 13 Day 6 先例）
- 只读：`src/commerce_agent/orchestration/bird_server.py`、`src/commerce_agent/evaluation/`（adapter 会话状态键复现面）
- Create: `docs/project/research/2026-09-15-cmode-phase-record-verdict.md`

**Steps:**
- [ ] 列出官方文件清单 → 用户确认 → 阅读
- [ ] 回答四问并留证据引用：① 官方 c 模式是否每轮新会话（对照 spool 实证）；② 官方每轮发给 agent 的上下文由哪些状态键构成；③ 任务问题（question 文本）在官方语义中是否应出现在每轮上下文（或仅首轮）；④ 我方 adapter 复现的键集与官方的差异点
- [ ] 定性结论：缺陷归属（我方 / 官方本义）+ 最小修复面清单

**判据:** 结论能解释全部历史 c 集（Pilot 9 + Run 2/3 + 验证 2）的失忆形态；每条论断有文件级证据引用。

---

### Task 2: c-mode 修复实现（零付费，红绿）

**Files:** 由 Task 1 定性决定——
- 若我方缺陷：`bird_server.py` / adapter 会话状态装配修复 + `tests/contract/test_bird_system_server_adapter.py` 红绿
- 若官方本义：`src/commerce_agent/context_builder/`（c-mode 每轮注入任务问题与已知澄清摘要；写明 GT 隔离论证——仅 question 公开文本）+ 单测红绿

**Steps:**
- [ ] RED：合成「16 轮会话」离线测试——断言每轮上下文含任务问题（或修复后的等价信息），agent 不再产生失忆句式
- [ ] GREEN：最小修复面实现；冻结契约三方一致性守卫保持全绿
- [ ] 离线端到端演练（FakeModel）：c 集 12–16 轮对话回放零失忆句式、submit 为真实 SQL 形态

**判据:** 离线演练对话与 Task 10 Run 3 / 验证重跑的失忆形态消失；全量 suite 无回归。

---

### Task 3: suite + 镜像 rebuild + 容器内实证（零付费，纪律门）

**Steps:**
- [ ] 全量默认 suite + Ruff（含 bird_system_agent 域）
- [ ] `docker compose -f compose.bird.yaml build bird-system-agent` → up
- [ ] 容器内实证：grep 新修复符号 + 配置 revision（坑 64 清单）

**判据:** 离线全绿 + 容器内修复代码在场。

---

### Task 4: c-mode 再验证（**付费 Gate C1 ≤$0.05**）

**前置:** Task 3 完成；用户对 C1 的独立授权；off-peak 窗口；官方库就绪探测（坑 67）。

**Steps:**
- [ ] 1–2 集 c-mode（新 experiment，archeology_scan_8 可复用同题（新 experiment 合法）或 seed 17 新题）
- [ ] 判据：① 对话回放零失忆句式、提交为真实 SQL；② **任意一集 reward>0 = 能力门 PASS**
- [ ] 产出 mini 报告入执行日志；fail → 有界诊断（≤1 轮）后停，交用户裁定

**判据:** reward>0 首分（能力门唯一判据）；或失败报告含完整证据链。

---

### Task 5: sim 模型合规静态排查 + a-mode SQL 诊断（零付费，裁定④）

**Steps:**
- [ ] sim 合规：官方 allowlist 内 simulator 配置面确认（sim 模型可换性 + 公平性条款 = c/a 同配置）；产出结论入研究笔记（若可换且用户批准，在 A4 启用前单独定案）
- [ ] a-mode 诊断：验证日 a 集 2 次提交的 SQL 与 Phase 1 判定复盘（agent 可见内容），归类失败原因（列错/聚合语义/格式），可修复项入 Task 2 同批或列为 A4 观察项

**判据:** 两项结论各成一节，有证据引用。

---

### Task 6: A4 清单生成 + 预算重估 + 四修复项（零付费）

**Steps:**
- [ ] 抽样清单：300/模式分层（数据库 × 歧义度 × 操作类型，seed 固定，§16.3 fallback 先例；Pilot/验证已跑任务在样本内则由 store 契约跳过不重跑）
- [ ] §16.4 重估表（精化锚：c $0.012 / a $0.023 / spent 7.40 元）：预计总账 vs 160 线，呈用户
- [ ] 四修复项落地：① `BIRD_EXPERIMENT_ID` 每实验注入 compose env（Runner 起 run 时写入）②同题双模式串行纪律入 Runner/preflight ③官方 orchestrator 子进程 stderr 落盘（agent 可见目录）④官方库就绪探测入 preflight 清单；各项红绿 + 入 `scripts/` preflight

**判据:** 清单 JSONL 公开入库；重估表数字齐；四修复项各有测试。

---

### Task 7: A4 Full 分批执行 + 消融（**付费 Gate C2**）

**前置:** Task 4 PASS；Task 6 重估表用户批准；C2 授权（含上限、熔断双条款、off-peak 窗口表）。

**Steps:**
- [ ] A4 分批执行：300/模式（concurrency 2，同题串行纪律），每批 off-peak 窗口；每 25 集重估入账本；越线安全暂停
- [ ] 消融 §17.2：30 task × c/a × A/B = 120 集（B 条件复用官方允许反馈修复）
- [ ] 每批 artifact：events JSONL + episodes + spool 导入（`BIRD_EXPERIMENT_ID` 已注入，join 直连）+ 运行目录

**判据:** 有效完成度如实披露；总账 ≤ 重估线；P1/P2/reward/轮次/费用分层可报告。

---

### Task 8: 产品 50 题（§17.1，零/低付费）

**Steps:**
- [ ] A/B 40 题 × 2 条件 + 封闭 10 题一次（产品轨，经产品闭环栈；模型费用并入 C2 口径列示）
- [ ] 报告正确率、Gold Recall、上下文 token、费用、延迟

**判据:** §17.1 预注册门槛与报告字段齐。

---

### Task 9: 收尾报告与 README/面试材料（零付费）

**Steps:**
- [ ] 最终报告：全链路对账（平台实扣 vs spool vs 账本）、三组实验结果、两验收门、限制与未完成项如实标注
- [ ] README（架构图、闭环演示、评测结果、复现指南）+ 面试材料
- [ ] HANDOFF 终版 + PowerContext 终版 handoff

**判据:** 「所有已完成数字可追溯；未完成项明确标记」（§Day 7 DoD）。

---

## 预算重估基线（Task 6 呈用户前的工作数字）

| 分项 | 精化锚 | 金额 |
|---|---|---|
| spent 至今 | 平台三实扣日 6.43+0.65+0.32 | **7.40 元** |
| A4 剩余 ~576 集（288c+288a） | c $0.012 / a $0.023 | ~$10.1 ≈ 71 元 |
| 消融 120 集 | 60c+60a | ~$2.1 ≈ 15 元 |
| 产品 90 episode | 产品轨，执行前细估 | ~23 元（沿 §16.4 锚） |
| **合计（基准）** | | **~116 元 vs 160 线 ✓（余 28%）** |
| 保守（+40% 单价上浮） | | ~155 元（贴线，靠每 25 集重估护栏） |

## 验收门（对齐 §Day 7 DoD）

- [ ] 能力门：c-mode 修复后任意一集 reward>0（Task 4）
- [ ] A4 Full：300/模式分层完成、成本 ≤ 重估线、artifact 可追溯（Task 7）
- [ ] 三组实验：§17.1/§17.2 报告齐（Task 7/8）
- [ ] 收尾：数字全可追溯、未完成项明确标记（Task 9）
