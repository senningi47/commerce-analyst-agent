# A4 a 批截停执行报告：223/300 + v4 验证 + 并发升档（2026-09-16）

> 执行者：Claude Code（GLM）；会话起于 12:21，截停裁定 22:45，收官 23:1x
> 授权链（每步独立）：① a 批照跑（~53 元）→ ② v4 验证授权（cap $0.20）→ ③ 并发升档「A」→ ④ GC7 同因两现停止（自动执行）→ ⑤ 「②」截停裁定（b10–b12 不跑）
> 本报告 commit 待用户授权。账本：`outputs/bird-budget/pilot-ledger.json`（`a4_a_batch_20260916`、`v4_validation_20260916` 节）。

## 1. 终态

| 项 | 值 |
|---|---|
| a 批结果 | **223/300 succeeded + 2 failed（确定性 `ContextBudgetExceeded`）+ 75 unrun（b10–b12，用户裁定②）** |
| reward | **0/225**（phase1_passed = 0，与知识缺口结论同形态，如实标注） |
| agent 成本 | **$6.150604 ≈ 43.5 元**（225 attempts / 2,301 turns，spool 聚合与 eval 视图 $6.1506 精确一致） |
| 单价 | $0.0273/attempt（vs 预估锚 $0.025，+9%；为 c 批 $0.0113 的 2.4×，来源 = schema/列语义渲染 + 更长对话） |
| price band | off_peak 225/225（12:31–22:45，跨午窗+晚窗，零 peak 泄漏） |
| sim 侧 | 不可见，待用户余额核对（a-mode 估 ~$0.002–0.004/集） |
| telemetry | **225/225 assigned，0 unassigned，0 ambiguous**（BIRD_EXPERIMENT_ID 接线首战全中，无 Task 10 式改挂） |
| 实验身份 | 10 个：`a4-a-20260916-b01…b09` + `-v4check`（config-hash 均为 `57eeec0f…` = a4/task-selection.json） |

## 2. 预算终账

- spent ≈ **75.9 元**（截停前 32.4 + a 批 43.5）
- 截停节省：75 × $0.0273 ≈ **$2.05 ≈ 14.5 元**
- forward：消融 17 + 产品 23 ≈ 40 元
- **总投影 ≈ 116 元 vs 160 线 ✓（余 ~28%）**（截停前投影 ~131）

## 3. v4 验证（授权 cap $0.20，实花 $0.056447）

**实现（零付费）**：`prompt-policies.v4`（bird-a-policy-v3：知识 miss → 向用户索要定义、禁止自造公式；common/retail/bird-c 逐字保留）+ bird_a profile v4 + 加载缝三处 + canonical 重写；RED→GREEN 3 守卫；888 passed；镜像 rebuild + 容器实证五项。

**验证**（3 集 `a4-a-20260916-v4check`，b02 前 3 题）：

| 判据 | 结果 |
|---|---|
| 核心 scenario（知识硬 miss → ask 定义） | 未触发——3 集知识工具全命中，零硬 miss |
| 广义 v4 行为（定义缺口 → 问而不造） | **2/3 激活，sim 真实作答**（MRS 分档 → "risk level 即 MRS 数值本身"；"困难情况" → "transport access minimal"），episode 2 将答案用于 SQL 并本地验证 |
| reward | 0/3（6 submit 全 "Test case execution failed"） |
| 副作用 | 无（1 ask/集；$0.0188/集，低于 a 批锚） |

**裁定**：回退 bird-a-policy-v2 跑批次（reward 中性 + 核心 scenario 未被样本触发 + 300 集单一策略版优先）；v4 归档 `outputs/bird-eval/prompt-policies.v4-20260916.json` 作已验证的将来杠杆；回退经 owning tests 21 passed + 容器实证。

## 4. 并发升档（规格偏差，已披露）

- 事实：`RunnerConfig.concurrency` 冻结 `le=2`（v0.3 §16.2 Day 1 spike 通过门），CLI 传 4 被 pydantic 拒绝（b08 首射零消耗失败）。
- 用户裁定「A」升 4；按「用户驱动显式偏差 + 全程披露」执行：`le=2→4` 一行 + cap 钉测试（4 可构造 / 5 拒绝）+ 全量 suite 887 passed + Ruff 绿；runner 为宿主进程，零镜像影响。
- §16.2 自身留有条件升档口（「并发 3 只有在错误率不升且 P95 明显改善时进入真实 Pilot」）——b08 即升档试点：**25/25 succeeded、零 infra、~18 分钟/批 vs 原 42 分钟（2.3×）**。
- 坑 63 同题双模式碰撞不适用（单模式异题批）。同题守卫（`validate_same_task_serial`）保持不变。

## 5. 上下文溢出：两现与定性（本轮新增机制发现）

- `fake_account_24`（b04，8 turns/$0.0665）与 `sports_events_8`（b09，10 turns/$0.0847）同因：第 ~10 轮 `_context_builder.build` 在裁剪至强制内容后仍超 bird_a `input_token_limit=65536` → `ContextBudgetExceeded` → agent 服务器 500 → `official_task_error`。
- 定性：**对话依赖的任务级确定性失败**（同任务同策略重试同位复发）；非 family 级（同库 fake_account_15/7/22/23 均成功）；非瞬时（GC7 补跑路径关闭）；非余额（provider 侧零 503）。
- 为何不修：64K prompt 上限 = 128K 模型窗口 − 64K 推理输出余量；后者是 runs d/e reasoning 爆炸的冻结修复，降输出预算即复发旧故障。修复风险 > 2 集损失。
- 处置：两集不进 sweep，标记「任务级确定性失败」如实入报告（rate 2/225 ≈ 0.9%）。

## 6. 截停裁定（用户，22:45）

选项②：停在 223/300。b10–b12（75 集，清单已派生未使用）不跑；数据集口径 = **c 批 300/300 全量 + a 批 223/300（74.3%）+ 2 集确定性失败 + 75 集裁定未跑**；c/a 同题配对维持 155 设计中的 ~10+75%…（b10–b12 未跑，配对数以 c/a 两清单交集 × 已跑比例为准，报告引用时按 eval 库实查）。

## 7. 现场还原与产物

- 栈与官方库已 stop（仅产品 PG 运行）；spike 6002 全程 Exited；migration head 仍 0007。
- gitignored 产物：events ×10、episodes 225+2（含 2 失败集空壳 + stderr 落盘）、spool +225 文件、账本两节、v4 归档、`agg_a4a.py`/`import_a4a_telemetry.py` 助手。
- 公开待 commit：本报告、执行日志 §32、`outputs/bird-pilot/a4/task-list-a-b01…b12/v4check/b02-rest.jsonl`、`runner.py` cap + `test_runner.py` 钉测试、HANDOFF/CLAUDE。
- **commit 全部待用户授权；未 push。**
