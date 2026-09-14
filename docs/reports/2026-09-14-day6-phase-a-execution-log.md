# 2026-09-14 执行日志：Day 6 Phase A（Task 1–4）与授权链

> 会话：2026-09-14（Asia/Shanghai）· 执行者：Claude Code（GLM）
> 范围：Day 6 计划批准与打包 commit 授权 → Task 1 提交语义排查、Task 2 spool 导入接线、Task 3 能力探针重设计（含付费执行）；Task 4–11 留待新会话
> 全程零 GT 内容读取；未 push

## 0. 授权链（用户决策记录）

1. 「批准Day6计划，每个Task都授权commit，不用逐次询问我」——计划批准 + 逐 Task commit 打包授权。
2. 深夜追加：「如果有需要我做决策的按你的推荐来，需要授权的我也提前授权执行，只要做好日志记录即可，在上下文窗口达到40%-50%前做收尾三件套并开一个新会话窗口去执行未完成任务」——付费 Gate（Task 3 探针 ≤$0.01、Task 10 小样本 ≤$0.10 含 sim 侧）、PG 写入、剩余决策全部预授权，日志记录为本日志。
3. 按授权行使的付费/DB 动作：① 探针真实调用 2 次（第 1 次脚本装配 bug 未完成校验但 token 已消耗，第 2 次完整通过；合计 <$0.001，远低于 $0.01 上限）；② `model_state.provider_turn` 删除 1 行 Day 4 测试种子残留（见 §3.3）。

## 1. Day 6 计划入库

- `b1c9a4e`：`docs/superpowers/plans/2026-09-14-day6-capability-restore-runner-and-ui.md`（11 Task 三线；Global Constraints 继承 Day 5 十一条 + 新增四条）。

## 2. Task 1：提交语义对齐排查（零付费静态，`1bdcf78`）

**交付**：`docs/project/research/2026-09-14-submit-semantics-alignment.md`。

**结论**：
1. 提交通道**无断裂**：官方评审在 db-environment `/submit` 端点（不在 orchestrator）；orchestrator 信任 system-agent 回报的 state（`total_reward`/`phase1_completed` 由我方 `_apply_submit_state` 维护——与官方 `tools.py` 逐行一致）。
2. Pilot d 实验 11 次 submit 逐条对拍：**全部到达 db_env 并返回结构化判定，0 次 `[exec_err_flg]`**——即全部「可执行但结果与 GT 不匹配」。
3. **根因 = 我方 prompt policy 缺失官方任务策略**：`bird-a-policy-v1` 无 9 工具成本清单与探索/验证/提交策略 tips；`bird-c-policy-v1` 无 `{max_turn}` 澄清上限声明。c-mode ask_user 539 次、a-mode 盲探索烧 coin，皆由此。
4. **Task 5 修复形状确定**：prompt-policies v2→v3（bird-a/bird-c body 整合官方策略要点，保持我方 envelope）+ c-mode 澄清预算闸（graph 层，携带原 call_id——坑 57）。无需动协议/状态机。

## 3. Task 2：spool 导入接线（TDD，`4ceab5c`）

**交付**：`src/commerce_agent/evaluation/spool_importer.py`（scan + assign + telemetry_patch）；`AttemptTelemetry` 新增 `agent_turns`/`agent_session_id`；store 协议与 PG/内存实现新增 `merge_telemetry`（jsonb `||` 合并）；agent 侧 `SpoolTraceGateway` 事件新增 `task_id`/`mode`/`experiment_id`/`recorded_at` 四字段；单测 10 + PG live 测试 1。

**设计要点**：
1. join 键：`(experiment_id, task_id, mode)` + 重试 attempt 的时间窗消歧（60s grace）；歧义 fail-closed（归 unassigned/ambiguous 披露，绝不猜）。
2. **d 实验旧格式 spool（785 文件）不含新标记，不可回填**——其聚合数字已在 Pilot 报告/账本固定；回填仅对 Day 6 之后的新 run 生效。原计划的「d 实验 20 attempt 回填演练」随之取消（无需 PG 写授权），以 PG live 测试（scoped fixture 自建自清理）替代。
3. 聚合纪律：一行坏即弃整个文件（fail-closed，与既有 `SpoolImporter` 精神一致）；token 恒等式在聚合下保持；`(price_snapshot_id, price_band)` 全文件一致才聚合。

### 3.3 测试环境修复（授权披露）

- **发现**：PG 套件 18 failed——① 漏设 `LANGGRAPH_STRICT_MSGPACK=true` 开关（17 个 checkpoint 测试，非缺陷）；② `model_state.provider_turn` 存在 **2026-09-06 02:00（Day 4 Gate W 时期）的测试种子残留 1 行**（attempt `00000000-…-4e20` 合成常量，与测试自身写入同键 → UniqueViolation）。
- **处置**：开关显式设置后 17 个测试全绿；残留行经只读查证（合成 attempt id + Day 4 时间戳，非 Pilot 数据）后精确 DELETE 1 行（admin 身份）。终态 PG 套件 **128 passed**。

## 4. Task 3：能力探针重设计（`636129c`，付费执行已授权）

**交付**：`scripts/probe_model_capability.py`（判据纯函数 `verify_probe_response` + 付费薄壳）+ 离线测试 7 项（全分支覆盖）+ 探针报告 `outputs/probe/capability-probe-report.json`。

**判据（每项对应一次真实踩坑）**：
1. `model_echo`：actual_model ∈ 快照（deepseek-flash 更名先例）
2. `tool_call_emitted`：契约工具（get_schema）真实触发 tool-calling（Task 13 缺陷⑥先例）
3. `usage_identities`：token 恒等式（reported）
4. `cost_recalculation`：快照重算 == 上报金额（Decimal 精确）+ band 判定（周日 off_peak 先例）
5. `attempt_accounting`：单 attempt success + 成本闭环（重试计费先例）

**付费执行记录**（授权 ≤$0.01）：真实调用 2 次——第 1 次因装配 bug（clock 协议、InMemoryProviderTurnStore 构造）在网关返回后校验层崩溃（token 已消耗，约 $0.0002 级）；修复后第 2 次完整通过，**成本 $0.000035334**。合计 <$0.001。**探针门从 FAIL（Day 3 固定 SQL 链）翻转为 PASS（Day 6 判据）**。

**新坑（坑 54 实证）**：探针脚本离线 7 测试全绿，真实运行连续暴露 3 个装配缺陷（clock 协议形状、turn_store 构造、窄快照 vs 全文件 json）——「凡首次真实 X」的预置可观测性纪律再次应验。

## 5. 当前状态与遗留

- 终态：离线 **846 passed, 129 skipped**；Ruff 全绿；PG `-m postgres` **128 passed**。commit：`b1c9a4e` → `1bdcf78` → `4ceab5c` → `636129c`（未 push）。
- **遗留（新会话按序执行）**：
  1. Task 4：v2 decide 三方一致性守卫（tests/contract/test_bird_tool_catalog.py 扩展）
  2. Task 5：c/a 策略修复（prompt-policies v3 + 澄清预算闸；Task 1 结论已给修复形状）
  3. Task 6：Runner SIGINT 中断恢复 E2E（双开关：`COMMERCE_AGENT_RUN_POSTGRES_TESTS=1` + `LANGGRAPH_STRICT_MSGPACK=true`）
  4. Task 7→8→9：SSE 事件面 → 关键 UI → 安全/E2E（`src/commerce_agent/api/`、`web/`、`tests/e2e/` 全新）
  5. Task 10（付费 ≤$0.10 已预授权）：小样本策略验证（新 experiment，c/a 各半，2–4 集）
  6. Task 11：Full 重设计方案对比（零付费分析，交用户裁定）
- 授权提醒：Task 10 是首次付费运行策略修复后的 episode；执行前确认 prompt-policies v3 离线全绿 + Task 5 红绿完成。

## 6. Task 4：v2 decide 三方一致性守卫（`84180e2`，新会话 2026-09-14 续）

**背景**：Gate P §6.5 技术债③——v2 `retail_decide` 规则提供 `request_clarification`/`submit_investigation_plan`，但图白名单 `_RETAIL_TOOL_NAMES` 仍是 v1 残留（含 `execute_readonly_sql`、缺这两个），legacy 图路径模型真实调用即 `tool_not_registered`。

**修复面（最小）**：
1. `retail_graph.py`：`_RETAIL_TOOL_NAMES`（v1 三工具）→ `RETAIL_TOOL_NAMES`（公开常量，与 v2 decide 规则四工具精确相等）。
2. `bird_c_responder.py`：内联 `{"ask_user","submit_sql"}` 提升为 `BIRD_C_TOOL_NAMES` 常量（查重点不变）。
3. `tests/contract/test_bird_tool_catalog.py`：2 → 6 测试——既有 BIRD 目录 ⊆ 契约两条保留，新增**三方精确相等**四条：bird_a 规则 == 端口 allowlist（同源冻结契约 fixture）、bird_c 规则 == 响应器常量、retail decide 规则 == 图 allowlist、图 allowlist ⊆ 冻结 retail 目录。
4. `tests/unit/orchestration/test_retail_graph.py`：resume 重试测试的 `execute_readonly_sql` 调用改为 `retrieve_retail_knowledge`（四工具白名单内），连带删除死代码 `AggregateExecutor` 与 QueryEngine/AstPolicy/QueryResult 导入。

**旧探针退役（`scripts/probe_deepseek_gateway.py` + 两个测试文件删除）**：全量 suite 首跑暴露 3 失败——旧探针 fake 路径在 decide 注入 `execute_readonly_sql`（`retail-probe-profile-v8` 合成规则），与新 allowlist 结构性冲突。退役依据：① `636129c` commit 信息已宣布「replace day 3 probe with day 6 capability probe」；② Gate P 正式结论「Day 3 固定 SQL 链判据不可行，需 Day 6 重设计」且 Task 3 已完成重设计（门 PASS）；③ 旧探针唯一调用面是其自身与两个测试。删除明细：`probe_deepseek_gateway.py`（928 行，tracked）、`tests/unit/scripts/test_probe_deepseek_gateway.py`（521 行，14 函数含一组 ×3 参数化 = 16 测试项，tracked）、`tests/integration/deepseek/test_retail_probe.py`（deepseek marker live 测试，**未跟踪文件**，磁盘删除）。`tests/integration/deepseek/__init__.py` 保留。

**数字闭合**：离线 846 + 4（新守卫）− 16（旧探针单测项）= **834 passed**；skip 129 − 1（deepseek live）= **128 skipped**；Ruff 全绿（src/tests/scripts/db/migrations/bird_system_agent）；PG `-m postgres` **128 passed**（checkpoint/resume 无回归）。

**披露（git 跟踪状态，待用户裁定）**：`src/commerce_agent/orchestration/bird_c_responder.py` 是**未跟踪文件**（Day 2 时代创建、从未入 allowlist，`git check-ignore` 确认非 gitignore；同状态还有 context_builder/builder.py、model/* 等核心模块——被跟踪测试早已导入未跟踪模块，属项目既有部分入库模式）。本 commit 未将其卷入窄主题 commit（守卫测试 `from ...bird_c_responder import BIRD_C_TOOL_NAMES` 与既有惯例一致）；若用户希望该模块（或其余未跟踪核心模块）入库，另行裁定。

## 7. 当前状态（Task 4 后）

- commit 链：`3a71357` → `b1c9a4e` → `1bdcf78` → `4ceab5c` → `636129c` → `de66c21` → `abb3beb` → **`84180e2`**（未 push）。
- 终态：离线 **834 passed, 128 skipped**；Ruff 全绿；PG **128 passed**。PowerContext 已由用户要求在本会话开场起动（端口 8000，`live: ok`）。
- **下一步**：Task 5（c/a 策略修复：prompt-policies v3 + c-mode 澄清预算闸 + a-mode 提交预留；Task 1 结论已定形状）→ Task 6（Runner SIGINT 演练）→ Task 7→8→9（SSE/UI/E2E）→ Task 10（付费 ≤$0.10 已预授权）→ Task 11（Full 重设计对比）。

## 8. Band 修正（用户输入，零付费，docs commit）

**用户澄清**：Pilot 余额核对的 6.43 元是**高峰时段计费**；此前用户转录的价格表是**空闲时段档**；并给出完整 CNY 价格表——off-peak 命中 ¥0.02 / 未命中 ¥1 / 输出 ¥4 每百万 tokens，peak 恰 2×（¥0.04/¥2/¥8）。

**核算链**：
1. §3.5（Pilot 报告）的「band 疑点查证排除」被推翻——`_pricing.py:46` weekday 门（周末硬编码 off-peak）+ 快照 evidence「01:00-04:00,06:00-10:00 weekdays」与平台实际计费不符（周日晚间按 peak 计费）。**band 模型缺陷成立**。
2. 换算基础：用户 CNY 表与快照 USD 列三档比值恒定 6.6667 → 平台 CNY 列 = 6.6667 × USD 列；修正统一用平台 CNY 口径（旧账本 7.07 指示性汇率与平台内嵌汇率并存，6% 差异如实披露）。
3. 重拆（Decimal 精确）：agent 实际 **3.74 元**（= 2 × 1.871，同一 token 量按 peak）+ sim 实际 **2.69 元**（6.43 − 3.74）= 6.43 ✓；**sim:agent = 0.72×（原 2.24× 反转）**；sim 单价 **$0.01008/集**（off-peak 基准；0.0672 / 0.1344 元 off-peak/peak）。
4. 两档外推：**A peak-run 461.4 元（2.88×）** / **B off-peak-run 233.9 元（1.46×）**——**均 FAIL**；旧 457.6 元为混合口径（agent off-peak + sim 误读 off-peak 反解），恰与 A 档接近但结构完全不同。
5. **结构性发现反转**：「a-mode Full 剩余单项超线」仅 peak 档成立（227.2 元）；off-peak 档 113.6 元不再单项越线。**off-peak 调度 = Task 11 首要降本杠杆**。
6. **Task 10 上限重估**：≤$0.10（2–4 集）peak 档 4 集预估 ~$0.20 越限、off-peak 档 ~$0.098 贴线——执行前须定档（推荐 off-peak）或经用户重确认。

**产物更新**：`outputs/bird-budget/pilot-ledger.json`（`balance_cross_check.band_correction_2026_09_14` + `projected_total_upper_bound_yuan=461.4` + `projected_total_scenario_off_peak_run_yuan=233.9` + decision 注记刷新；历史值保留 superseded 字段）；Pilot 报告 §0 指针 + §7 修正划线 + 新增 §8；HANDOFF §2.10 修正段 + §2.13 第 5 项；CLAUDE.md §0。**零付费、零 GT 读取、无代码变更**（`_pricing.py` 修复与快照 v5 重冻结待用户提供真实峰值窗口后独立红绿执行）。

> **⚠ §8 状态：已于同日撤回**——建立在该轮「peak 计费」用户口误之上，见 §9。

## 9. Band 撤回与恢复（2026-09-14 同日稍晚，docs commit）

**用户第二轮输入**：官方峰值窗口定义 + 定价页截图——**高峰 = 北京周一至五 09:00–12:00、14:00–18:00，其余全为空闲档**；并自述「忘记运行当天是周日了」→ 撤回「6.43 元为高峰计费」的说法。

**核实**：官方定义与快照 v4 **精确一致**（`peak_windows_utc` UTC 01–04/06–10 = 北京 09–12/14–18；evidence「weekdays」；`_pricing.py:46` weekday 门）——**band 模型自始正确**，快照与代码零改动；band 判定获得双源确认（api-docs USD 页 + 用户 CNY 定价页）。周日全天空闲档 → Pilot 全部消耗（d + 诊断）确为 off-peak 计费。

**恢复**：§3.5 原对账与 457.6 元 FAIL 终裁全部恢复有效（sim $0.6288 / 2.24× / $0.03144 每集 / a-mode 单项 210 元超线）；§8 的拆分、两档外推、结构反转、Task 10 上限重估**全部作废**。账本恢复 457.6 并新增 `band_claim_withdrawal_2026_09_14`（含 `projected_total_peak_run_penalty_yuan=908.8`）。HANDOFF §2.10/§2.13、CLAUDE.md §0、Pilot 报告 §0/§7 同步为最终口径。

**存续洞见 → 运行纪律**：peak 档恰为 off-peak 的 2×；付费运行（Task 10 小样本、未来 Full/消融）**一律调度空闲档**（北京工作日夜/晨、周末），否则剩余翻倍至 ~908.8 元（5.68×）。Pilot（周日）已天然满足；汇率口径注记（平台 CNY 列 = 6.6667×USD 列，总账对两种汇率口径不敏感：457.3 vs 457.6）随账本记录。零付费、零代码变更。

## 10. Task 5：c/a 策略修复（`ad961e8`，同会话续）

**输入**：Task 1 研究笔记 §5（修复形状：prompt-policies v3 + c-mode 澄清预算闸；根因 = 我方 prompt 缺官方任务策略，通道无缺陷）。零付费、零 GT 读取。

**修复面（全部策略层，未动 submit 链路/`_apply_submit_state`/BudgetStopGate/`_submitted_this_phase`）**：
1. **`configs/model/prompt-policies.v3.json`**（新增，canonical 单行）：`bird-a-policy-v2` = 原 3 句隔离 envelope **原样保留** + 官方策略整合——9 工具 coin 成本逐项列出（与冻结契约逐项断言相等，防漂移）、探索先行（schema/列含义/外部知识）、**先 execute_sql 验证再 submit_sql**、失败且预算有余则 debug 重试、P2 同纪律；`bird-c-policy-v2` = 原 2 句 envelope 原样保留 + `max_turn` 澄清上限声明（每轮恰一问、预算耗尽必须 submit）。`retail-policy-v2` 与 common envelope 零改动（三轨不交叉测试全绿）。**官方预算澄清**：a-mode 初始预算非固定 18，而是 `6 + 2×歧义数 + 2×patience`（`ainteract.py:45-54`，任务相关存 state）——policy 引导锚定 `budget_remaining` 而非硬编码。
2. **`run-profiles.v2.json`**：bird_a/bird_c 的 `prompt_policy_revision` → v2 策略名（profile revision 沿 Task 13 先例不 bump）。
3. **c-mode 澄清预算闸（graph 层，行为保险）**：`BirdSessionStatePort` 新增 `_gate_clarification_budget`——官方 state 键 `max_turn`（orchestrator 按 `n_ambiguities + patience` 种入，`cinteract.py:115-124` 一手核验）；预算耗尽后的 ask_user **替换为提醒结果**（对齐官方 before_model_callback 语义，搭载原 call_id——坑 57）；提醒经 **ask_user 的 answer 通道**回流（`_run_c` 的 `_answer_text` 提取 `answer` 键——a-mode gate 的 `text` 键在该通道会提取成空串，实现期发现的坑）；每轮 phase datum 注入实时预算行 `[clarification budget: used of max_turn ...]`；`_ask_user_turns` 与 `_submitted_this_phase` 同点按 phase 重置。max_turn 缺失/非正 → gate 关闭（prompt-only，等价 Task 5 前行为）。
4. **guard**：`tests/contract/test_bird_prompt_policy.py` 新增 6 条（envelope 前缀保持 ×2、coin 成本==冻结契约、策略要点存在 ×2、retail policy 不受沾染）。

**TDD 红绿**：RED 7 failed（3 policy + 4 gate，含「60 轮全 ask_user」最小复现——`max_turn=2` 下 EndlessAskHandler 应只执行 2 次 ask_user）→ GREEN 后 210 passed（contract+builder+orchestration）。测试期修正一处断言索引：gate 提醒出现在**下一轮**请求的 phase datum（gating 发生于模型第 3 轮仍选 ask_user 之后），非当轮。

**判据达成**：离线演练 `test_c_clarification_budget_gated_ask_reaches_submit_within_cap`——2 次澄清 + 1 次被闸提醒 + submit_sql，`model_turns=4`，dialogue_history 零污染（被闸的第三次提问不入对话史）；c-mode 每集至少一次 submit_sql 的修复后行为模式成立。全量离线 **844 passed, 128 skipped**（834+10）；Ruff 全绿；PG `-m postgres` **128 passed**。配置面变更经 `compute_config_hash` 自动流入新 experiment 的 config_hash（无硬编码哈希破坏）。

**Step 4：c-mode 成本敏感度表（供 Task 11，入账本 `task5_c_mode_sensitivity`）**：实测锚定（60 轮：agent $0.0100 + sim $0.03144 每集，线性模型）→ 每轮合计 $0.000691；修复后轮次 ≈ max_turn+1~2：

| N（模型轮） | 每集 agent+sim | c 侧 740 集合计 |
|---|---|---|
| 5 | $0.0035（0.024 元） | $2.56（18.1 元） |
| 8 | $0.0055（0.039 元） | $4.09（28.9 元） |
| **10** | **$0.0069（0.049 元）** | **$5.11（36.1 元）** |
| 15 | $0.0104（0.073 元） | $7.67（54.2 元） |
| 20 | $0.0138（0.098 元） | $10.22（72.3 元） |
| 60（Pilot 实况） | $0.0414（0.293 元） | $30.67（216.8 元） |

N=10 时 c 侧较 Pilot 失控基线 **-83%**。a-mode 无行为级基线拆分，预期改善（coin 策略引导探索收敛）待 Task 10 实测。

**下一步**：Task 6（Runner SIGINT 中断恢复 E2E，双开关）→ Task 7→8→9（SSE/UI/E2E）→ Task 10（付费 ≤$0.10，空闲档运行）→ Task 11（Full 重设计对比）。

## 11. Task 6：Runner SIGINT 中断恢复 E2E（`f6af9f1`，同会话续，零付费）

**交付**：`tests/integration/evaluation/test_runner_sigint_recovery.py`（`-m postgres`，PG 真库 + 脚本化 executor，1.99s）。Day 6 验收门②证据，与 §19.3 末条逐字对应。

**演练设计**（4 题清单，concurrency=1 确定性串行）：
1. **Run 1（中断）**：`sigint-a1`/`sigint-b2` 立即 succeed；`sigint-hang` 在 executor 内 `stop_event.set()` 后挂起——这是 `cli._bridge_signals` 收到 SIGINT/SIGTERM 后 `stop_event.set()` 的确定性等价物；`sigint-p4` 因信号先到而**从未被 claim**。断言：`summary.stopped=True`、attempted=3、status_counts={succeeded:2, interrupted:1}；**eval 表恰好 3 行**（hang 行 `interrupted`，p4 无任何 attempt 行——`finish_attempt` 对未注册 id 抛 EvalStateConflict 被 `_mark_abandoned` 捕获，恰好是 pending 题的正确形态）。
2. **Run 2（同 experiment 重启）**：completed 不重跑 ✓；interrupted 题以 **attempt_seq=2 从头跑**并 succeed ✓；pending 题以 seq=1 正常跑 ✓；总账 4 completed / 0 unfinished。
3. **交叉断言**：事件 JSONL 全 10 条序列精确（含 `attempt_interrupted` 不带 status 键——事件类型即信号；seq=2 的 started 带新 run_id）；`experiment_rows` 只读查询逐行核对两个 run 的状态机终态。
4. **finally 语义**（坑 6 同款）：fixture 按序 DELETE task_result → task_attempt → experiment（admin DSN），完成后该 experiment 行数归零。

**红绿说明**：Day 5 的状态机/停止机制（gather-vs-stop 竞速修复、`_mark_abandoned`、恢复序列）已被单测钉死；本演练为集成级验收证据——首跑仅 1 处测试断言错误（误以为 `attempt_interrupted` 事件携带 `status` 字段，实现上事件类型即状态信号），修正后即绿；产品代码零改动。

**终态**：PG `-m postgres` **129 passed**（+1）；离线 **844 passed, 129 skipped**（新 PG 测试默认 skip +1）；Ruff 全绿。

**下一步**：Task 7（SSE 事件面 `src/commerce_agent/api/` 全新）→ Task 8（关键 UI `web/`）→ Task 9（安全/E2E）→ Task 10（付费 ≤$0.10，空闲档）→ Task 11（Full 重设计对比）。

## 12. Task 7：SSE 事件面（`3003ca5`，同会话续，零付费）

**交付**：`src/commerce_agent/api/`（app 工厂 + events schema + sse 端点）+ **migration 0006** + 离线单测 11 条 + PG 判据测试 3 条。fastapi 0.141.1 入依赖（httpx 已在 lock）。

**设计决策（三个关键）**：
1. **事件面 = Product Trace 的 11 种 `TraceEventType`**（计划候选名映射：approval_required≡proposal_created、approval_decided≡decision_recorded、execution_receipt≡execution_completed、report_ready≡report_completed、run_resumed≡recovery_applied、sql_rejected 经 status+reason_code 表达）——推送序列与产品闭环审计**按构造一致**；载荷排除 node/phase（§20 前端不依赖 LangGraph 节点名）。
2. **隐私 = 视图即白名单**：migration 0006 建 `ops_read.product_trace_events`（security_barrier，恰 8 列公开面）授权**现有 `agent_reader`**（`PRODUCT_DATABASE_DSN` 即该身份）——不动基表授权（trace_writer 保持 append-only、agent_reader 保持无基表 SELECT，Day 4 ACL 测试不破）。载荷主体 = trace 表预计算的 `safe_summary`（Day 4 已定公开面）。
3. **游标 = row_number() 稳定序**：trace 的 `sequence` 是 attempt 内序号（恢复事件在新 attempt seq=0，直接用会撞号）——SSE `id` 用 `(occurred_at, attempt_id, sequence)` 排序的 run 级 row_number，Last-Event-ID 按它重放；载荷保留原 sequence 供审计对账。

**过程中的坑（3 个，全部实证）**：
- **starlette 1.6 TestClient 缓冲整个响应体**（`portal.call(self.app…)` 跑到完成为止）——无限 SSE 流经 `client.stream()` 必挂（最小对照用例的「成功」是 3 块后生成器自然结束的假象）。流式行为改为**直接驱动 async 生成器**测试；Last-Event-ID 解析抽纯函数单测；PG 判据同法直驱真源。
- **alembic revision id ≤32 字符**：首版 revision 33 字符，`UPDATE alembic_version` 超出 varchar(32) 报 StringDataRightTruncation——事务性 DDL 整体回滚、目录零漂移，缩短 id 重跑成功。已写进 migration docstring。
- **TestClient 对纯 fake 的流式单测**同样受第 1 条约束——挂起超时两轮定位（faulthandler 栈：portal 线程 Proactor `_poll` 空转 + 主线程等 response start）。

**PG 判据（`tests/integration/api/test_trace_sse_pg.py`，1.56s）**：真实 `PostgresTraceStore` 写入完整闭环 13 事件（澄清→计划→SQL→修复→执行→对账→提案 v1（未审批）→拒绝→提案 v2→批准→执行→报告 + 新 attempt 恢复）→ 经 `PostgresTraceEventSource`（agent_reader 身份）消费 `sse_stream`：推送序列与审计**逐条一致**（ids 1–13、拒绝决策摘要原文透出）；`Last-Event-ID: 6` 只推 7–13；视图列白名单 == 8 列。finally 场景 reset 归零 + 连接关闭守卫通过。

**终态**：离线 **855 passed, 132 skipped**（844+11 / 129+3）；Ruff 全绿；PG `-m postgres` **132 passed**（129+3）；migration head = `0006_day6_trace_read_view`（PG 写入预授权范围内应用）。

**下一步**：Task 8（关键 UI `web/` 全新：模拟数据 Demo → SSE 客户端 → 评测中心只读页）→ Task 9（安全/E2E）→ Task 10（付费 ≤$0.10，空闲档）→ Task 11（Full 重设计对比）。
