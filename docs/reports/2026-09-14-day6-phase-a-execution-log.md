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

## 13. Task 8 Step 1：Demo 布局（`79de29b` + `7d3881e`，同会话续，零付费）

**交付**：`web/`（Vite 7 + React 19 + TypeScript 5.9 + ECharts 5.6，独立 lockfile）——两轮：初版 Demo（`79de29b`）→ 用户定位反馈后按目标用户重做（`7d3881e`）→ **用户确认布局满意（Step 1 计划门通过）**。

**目标用户定位（用户提问后裁定）**：电商公司运营团队三角色——①**运营分析师**（一线：提问/看结论/发起提案，非 SQL 专家但要可信）；②**运营负责人**（审批：预算动作的 Diff/预算影响/决策依据）；③**平台/数据团队**（评测中心：reward/费用/轮次）。

**第一轮（`79de29b`）**：手写脚手架（避免交互式 CLI）+ 16 文件 Demo——脚本化闭环事件流（与 Task 7 SSE 同 11 种事件词表）驱动 §20 全 10 状态（状态条点选重放前缀 / 自动播放 700ms 步进）；三页签（工作台三栏 / 审批 / 评测中心）。`tsc + vite build` 干净；Chrome 实机验证状态切换。构建坑：echarts 误写 ^1.0.0（应为 ^5.6.0）+ tsconfig.tsbuildinfo 漏 ignore（已补）。

**用户反馈 → 重做（`7d3881e`，调 `minimalist-ui` skill）**：初版被用户指出偏「工程师视角」。重设计要点：
1. **工作台答案视角**：提问栏 + 3 建议问题（快速上手）→ 衬线体结论大字置顶 → 三张指标卡（语义色标跌）→ 带来源标注的图表 → 提案条（去审批 →）→ 「分析过程」「数据与证据」两个折叠区（SQL 版本含拒绝原因与来源表、结果表、对账、知识来源芯片）→ 右侧紧凑审计栏；演示控制条降为次级灰条。
2. **审批页队列制**：待我审批(1) → 标题/发起人/类型/**预算影响（占月度预算 %）** → Diff（删除线红/新增绿）→ 决策依据 → **驳回必填标注门控**（实测生效）→ HMAC 身份注记。
3. **评测中心**：统计卡（20 集/18 完成/reward 0 红/¥1.4932）+ Pilot 实测明细表 + 两门基线注记。
4. **风格（minimalist-ui 规范）**：暖白 #F7F6F3 底、白卡 1px #EAEAEA、衬线标题/无衬线正文/等宽数字、四种低饱和粉彩语义色、无 emoji 无阴影、黑底主按钮。
5. 实机验收：Chrome 实测工作台完成态 + 审批待批态（驳回门控生效），用户满意。

**给 Step 2/3 的设计约束（新会话必读）**：
- 模拟载荷（SQL 文本/表格/图表）是 **demo-only**——真实 SSE 是 summary-only（§18），工作台的富内容需要**只读工件 API** 决策（哪些工件公开、如何从 trace/查询结果取）。
- `buildView(events)` 投影函数为真实 SSE 事件复用而设计；`stageOf(events)` 是真实事件→状态推导的种子。
- 评测中心真实数据需要 **eval schema 读权限**（现无任何角色可 SELECT eval 表——需 migration 0007 授权或等效方案，延续 0006 的视图+现有角色模式）。
- dev server 已停（`cd web && npm run dev` 重启，端口 5173）。

**终态**：Task 8 Step 1 完成（用户满意）；Step 2/3 未开始。离线 **855 passed, 132 skipped**；PG **132 passed**；Ruff 全绿（web 侧独立 toolchain，不入 Python suite）。本会话 commit 链（自 Task 4 起）：`84180e2`→`9f7f10f`→`211648a`→`2a2046b`→`ad961e8`→`c782287`→`f6af9f1`→`3ee58f1`→`3003ca5`→`494600c`→`79de29b`→`7d3881e`（12 个，未 push）。

## 14. Task 8 Step 2/3：SSE 客户端 + 只读 eval API + 评测中心真实数据（新会话 2026-09-14 续，零付费）

**输入**：HANDOFF §2.17 Step 2/3 必读约束四条 + 用户打包授权（剩余决策按推荐行使、PG 写入、逐 Task commit）。现场核验：产品源码中 `retail_graph` 持有 trace 端口但尚未写入（Task 7 PG 判据是直接经 `PostgresTraceStore` 驱动）——印证 live 模式必须按 summary-only 事件面设计。

**工件 API 决策（Step 1 遗留①，按授权行使推荐）**：live 模式 v1 = **summary-only 事件面直驱**——事件公开面即工件（decision_summary 文本 → 结论/计划/对账、SQL 双指纹 + reason_code → 查询版本、evidence chips、proposal/execution refs）；**富工件（SQL 文本/结果表/图表）不持久化、不新增工件写路径**，推迟到 Day 7（需 QueryEngine 写路径扩展 + 新 ACL，超出 §22 UI 收窄）。演示模式保留完整富布局（Step 1 已验收）。

**交付（四层）**：
1. **migration 0007**（`0007_day6_eval_read_view`，已应用）：`ops_read.eval_experiments` + `ops_read.eval_attempts`（security_barrier，attempt 视图 = attempt+result LEFT JOIN + telemetry 白名单三列 agent_cost_amount/simulator_cost_amount/agent_turns）；**视图 owner = evaluation_owner**（底层权限检查走 eval owner → 基表 ACL 零改动，比 0006 的 ops_owner 方案更严）；SELECT 授权现有 `agent_reader`。revision id 24 字符（≤32 坑已内化）。
2. **API**（`src/commerce_agent/api/`）：`eval.py`（experiments 聚合 + attempts 明细两端点，行映射纯函数可离线测）、`runs.py`（`/api/runs` 运行目录）、`app.py` 扩展（可选源注入 + `create_postgres_app` env 装配）+ `scripts/run_api.py`（win32 启动器，见新坑 59）+ `scripts/seed_ui_live_run.py`（浏览器验证用一次性种子，验证后已场景 reset 归零）。uvicorn 0.52.4 入依赖（fastapi 的服务标准件）。
3. **web**：`api.ts`（REST + `openRunStream`——服务端事件块带 `event:` 字段故按 11 种类型 `addEventListener`；EventSource 原生携带 Last-Event-ID 重连）、`view.ts`（真实 summary-only 事件 → 视图模型投影 + 共享 `stageOf`（未映射事件返回 null，UI 回退显示事件类型本身））、`App.tsx`（演示/实时双模式；实时 = 运行下拉选择 → SSE 订阅 → 同一 `buildView` 投影）、Workbench（无 SQL 文本时渲染指纹芯片、无 meta 提案显示 ref、无答案澄清不虚构）、EvalCenterPage（真实 eval API + 断连时演示快照并明确标注）、vite `/api` 代理。
4. **测试**：migration 源测试 5（白名单逐列 + 授权语句字面钉死 + revision 长度）；api 单测 6（行映射/端点/可选源 404 面）；PG 集成 5（agent_reader 身份读真实行 + 视图列白名单 == 期望 + **负向 ACL：agent_reader 读 eval 基表 InsufficientPrivilege** + runs 目录真实 trace 行）。

**过程中的坑（3 个新坑，全部实证，详见 HANDOFF §6.9）**：
- **坑 59**：uvicorn 0.52 win32 硬编码 `ProactorEventLoop` 工厂（`use_subprocess=False` 时），`asyncio.set_event_loop_policy` 与 factory 内设置**都太晚**（psycopg async 拒绝 Proactor）——离线/PG 测试全绿、首次真实服务起动即炸（坑 54 再验）。唯一干净接缝：启动器自管 `asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)` 驱动 `Server.serve()`。
- **坑 60**：vite 7 默认只绑 IPv6 `::1`——`127.0.0.1:5173` connection refused（白页）；且 vite 代理对上游断开传播有延迟（后端已杀，浏览器 SSE 短时仍显示「已连接」）。演示用 `localhost` URL；断连显示时效如实记录（直连后端无此层）。
- **坑 61**：React 19 dev StrictMode 双挂载会把事件流回放**双份**入 state（#10 ×4）——服务端 Last-Event-ID 重放语义正确也不能免；客户端按 cursor 去重后精确 10/10。

**浏览器实机验证（IAB，Chrome 内核）**：演示模式回归完整（15/15 事件）；评测中心**真实 Pilot d 数据逐格核对一致**（20 集 c×10/a×10、18 成功、1 failed official_task_error=crypto_exchange_9、1 基础设施错误 official_process_failed=cybermarket_pattern_12、rewards 全 0 如实展示、`$0.0000` 成本卡诚实标注「spool 未回填（旧格式）· 实际消耗见 Pilot 账本」）；实时模式选 run → SSE 10 事件流入 → 投影正确（结论/提案 ref/计划步骤/审计轨）→ **杀后端 → 重启 → EventSource 自动重连，仍恰好 10 事件无重复**（服务端 cursor 重放 + 客户端去重双保险）。截图两帧留档。

**现场清理**：种子场景 `day6-ui-live-v1` 场景 reset 归零（seeded trace 0 行）；eval 库 4 实验为 Pilot 原有数据未动；dev server 与 API 进程已停。零付费、零 GT 读取。

**终态**：离线 **866 passed, 137 skipped**（855+11：migration 5 + api 6；skip 132+5 = 新 PG 测试离线 skip）；Ruff 全绿；PG `-m postgres` **137 passed**（132+5）；`npm run build` 干净；migration head = `0007_day6_eval_read_view`。Task 8 全部三步完成（Step 1 用户门 + Step 2/3 本轮）。**下一步：Task 9（tests/e2e 安全/E2E，验收门①成文）→ Task 10（付费 ≤$0.10，空闲档）→ Task 11（Full 重设计对比）。**

## 15. Task 9：安全/E2E（`tests/e2e/`，同会话续，零付费）

**交付**：`tests/e2e/test_product_chain_api.py`（PG E2E 3 条）+ `tests/e2e/test_ui_acceptance.md`（手工验收清单）+ `tests/e2e/conftest.py`（win32 selector policy + postgres skip 门控，镜像 integration/conftest 的两块承重件）。

**E2E 设计（纯读侧 + 真实装配）**：
1. **三闭环审计 fixtures**：经营调查 13 事件（澄清→计划→SQL 生成→修复→执行→对账→提案 v1→驳回→提案 v2→批准→受控执行（execution_ref+audit_ref 读回关联）→报告 + 新 attempt 恢复）经真实 `PostgresTraceStore`（trace_writer）写入三个 scoped 场景；卖家风险/指标预警两闭环各 3 事件（proposal→approval→execution+audit_ref）。
2. **消费只走 agent_reader**：`create_app` 全三源真实装配（TraceEventSource/EvalSource/RunDirectory）——REST 经 TestClient（runs/eval 端点），SSE 直驱生成器（TestClient 缓冲限制，Task 7 已钉死）。
3. **判据**：①工作台链路 REST+SSE 全程——/api/runs 发现 13 事件 run、SSE 推送序列==审计序列（ids 1–13）、驳回/批准决策码、执行事件带 execution_ref+audit_ref、报告码 report_ready；②三闭环各一次审批/执行/读回；③评测中心 REST 读回（experiment 聚合 + attempt 明细 reward/P1/turns）；④**载荷白名单**：每条 SSE data 键集 ⊆ §18 十六键、无 node/phase。
4. **Playwright 决策（按授权行使推荐）**：不引入——§21 必选清单未含、IAB 实机验证已在 Task 8 完成、CI 无浏览器需求；清单中留 Day 7 裁定口。

**过程修正（2 处，测试自身）**：`_write_audit` 在 async fixture 内误用 `asyncio.Runner`（running loop 冲突）→ 改 async 直驱；审计 fixtures 全部同一时间戳导致 SSE 排序键 `(occurred_at, attempt_id, sequence)` 把 recovery 按 attempt_id 排到最前 → 时间随事件递增（recovery +100s）。

**判据达成（Task 9 完成即 Day 6 两验收门齐备）**：验收门①「主产品链路可演示」= 手工清单（`test_ui_acceptance.md`，演示模式 + 实时模式 + 评测中心 + 隐私红线四节）且 Task 8 Step 2/3 已实机走通；验收门②「Runner 中断恢复」= Task 6 SIGINT 演练 PASS。PG 全套（integration+e2e）**140 passed**；离线 **866 passed, 140 skipped**（e2e 3 条默认 skip）；Ruff 全绿。

**下一步**：Task 10（付费 ≤$0.10 已预授权——**空闲档运行**，新 experiment，c/a 各半 2–4 集，选 Pilot 同库不同题避免记忆污染；执行前确认 prompt-policies v3 全绿[已达成]）→ Task 11（Full 重设计方案对比，交用户裁定）。

## 16. Task 10 零付费准备完成（同会话续；付费运行待空闲档，未执行）

**空闲档判定（运行纪律，§9）**：选题主会话现场时钟 = 北京周一 14:43，**peak 窗口内**（工作日 09–12/14–18）。off-peak 2 集预估 ~$0.057，peak 同量 ~$0.114 **超出 ≤$0.10 授权上限**——付费运行不执行，待 18:00 后空闲档（或周末）。

**预算算术（选题依据）**：修复后 c 集（N≈10 轮）agent+sim ≈$0.0069/集（Task 5 敏感度表）；a 集 agent p95 $0.0188 + sim $0.03144 ≈$0.0502/集（Pilot 实测）。c2/a2 ≈$0.114 越限 → **取下限 2 集（c1/a1）**，off-peak 预估 ~$0.057，cap 余量 ~2×。

**选题（`prepare_bird_pilot.py --count 2 --seed 11 --out-dir outputs/bird-pilot/task10`，零 API）**：`archeology_scan_M_4` c+a **同题双模式**（同 GT 直接对照两模式，比异题更强）；库 = archeology_scan（Pilot 同库不同题：Pilot 用 M_1，零任务重叠 → 记忆安全）。ambiguity_count=3（a-mode 预算 = 6+2×3+2×patience，任务相关）。产物：`task-selection.json` + `task-list.jsonl`（公开，入库）；`task10/task-data/`（GT 拆分，gitignored——**新坑实例：GT 守卫对新 out-dir fail-closed 拦截，`.gitignore` 精确补 `task10/task-data/` 与 `bird-pilot/bird-budget/` 后通过**）。Pilot 账本（`outputs/bird-budget/pilot-ledger.json`）核验完好未触碰；新 seed 账本落 `outputs/bird-pilot/bird-budget/`（gitignored）。

**18:00 后一键执行清单（新会话按此跑）**：
1. 确认空闲档（北京工作日夜/晨或周末）+ spike 容器 6002 已 stop + compose.bird 栈三服务起动。
2. **现场重算 config-hash**（Task 5 后配置面已变：prompt-policies v3 + run-profiles bird 规则；Pilot 旧值 `b1889777…` **失效不得复用**）。参考指纹（本会话用 `compute_config_hash(profile, NonThinkingConfig(disabled), "deepseek-flash")` 现算）：bird_a `b4a9505b7841d765b8bf8140c30fabd5d3be49f93557c2b1e3f7705cc6e7242d`、bird_c `3bfd579a9a9632c48c22ea2cc53c01ee95d1bedd2a9a9cf8ba4a330eb1feb7f8`——**执行时按 Task 13 同款派生方式定实验级单值并记录**。
3. Runner 命令（Task 13 模式）：`uv run --env-file .env python -m commerce_agent.evaluation --experiment task10-strategy-validate-20260914 --purpose ablation_repair --config-hash <现场计算> --task-list outputs/bird-pilot/task10/task-list.jsonl --events outputs/bird-eval/events-task10.jsonl --executor official --store postgres --adk-root _upstream/BIRD-Interact/BIRD-Interact-ADK --task-data-dir outputs/bird-pilot/task10/task-data --episode-output-dir outputs/bird-eval/episodes`。
4. 监控：双时点快照（spool 文件数/成本两拍，坑 58）；熔断双条款按 Gate P 文档；上限 $0.10 硬线。
5. 判据：c-mode submit_sql ≥1/集；**reward 首分 > 0 即通道证明**；每集 agent+sim 新成本读数 → 产出验证报告（策略修复前后对比）交 Task 11。

## 17. Task 10 执行：三次运行、两基础设施发现、submit 通道贯通（同会话续，23:08–24:00 空闲档）

**按 §16 清单执行**（用户指示「按 §16 清单执行 Task 10」）。完整细节见 `docs/reports/2026-09-14-task10-strategy-validation.md`；账本 `outputs/bird-budget/pilot-ledger.json` 新增 `task10_strategy_validation` 节。零 GT 读取；agent 实测 $0.025047 + sim 估 $0.05–0.08（余额核对终裁）。

1. **准备**：空闲档 ✓；spike 6002 已 stop ✓；compose.bird 三服务 + 官方库 5433 起动。**config-hash 派生方式复核确认 = task-selection.json SHA-256**（Task 13 旧值精确复现），实验级单值 `97a40e74…ad749`；两个 per-profile 参考指纹现场复算与 §16 **精确一致**。
2. **Run 1（`…20260914`，a+c 并发 2）**：c 集 2.4s failed——db-env `/init_task` 500，**同题双模式并发对同一 task DB drop/create 竞态**（官方 `create_task_db` 命名不含 mode；Task 13 异题故未触发）；a 集 succeeded（116s）但第 8 次模型调用被网关 fail-closed `provider_response_invalid` 中断（前 7 轮 reported，行为形态好：探索+知识+SQL+2 澄清，13/18 coin）。a 保留原判（轨迹可用，重跑破线）；c 按 GC7 + Task 13 字母后缀先例补跑。
3. **Run 2（`…b`，c 单集 concurrency=1）**：succeeded 但 **60 ask / 0 submit / 551s**——Pilot 失控形态复现。**根因 = 镜像陈旧**：`bird-system-agent` 镜像烘焙于 09-13（Task 13 期），Task 5 闸与 v3 配置从未入 live（容器内 grep 0 命中、仅 v1 配置）。Run 2 测的是 Pilot 代码；修复「无效」被排除。
4. **镜像重建 + Run 3（`…c`）**：rebuild 后容器内实证（gate 2 命中、v3 存在）→ c 补跑 **succeeded 118s：12 ask_user + 2 submit_sql，两次提交到达官方评审返回结构化 Phase 1 判定（failed，reward 0）**；dialogue 24 条零拦截（12 ≤ max_turn，模型自主提交）。**判据：submit_sql ≥1/集 ✓；reward >0 ✗（SQL 质量，非通道）**。
5. **成本/遥测**：agent $0.025047（0.011539890 + 0 + 0.009389802 + 0.004117392）；sim 估 $0.05–0.08 → 合计估 $0.075–0.105（上限边界，本 episode 不再付费）。band 全程 off_peak ✓。Run 3 遥测经 Task 2 importer 入库（**首次真实使用**；`BIRD_EXPERIMENT_ID` compose 默认值 → 确定性改挂，披露为 Day 7 接线缺口）；Run 1/2 旧格式不可回填。
6. **现场**：compose 栈 + 官方库已 stop（回到开场前）；eval 库新增 4 个 task10 attempt（证据保留）；migration 未动（head 0007）；无代码变更（纯 ops + docs + 新增公开 `outputs/bird-pilot/task10/task-list-b-c1.jsonl`）。

## 18. Task 11：Full 重设计方案对比（`6ef0c91`，新会话 2026-09-15，零付费，交用户裁定）

**Day 6 收官 Task**（用户指示：读 HANDOFF §0/§2.20/§3/§5/§6.10 + Task 10 报告，执行 Task 11 后停下等裁定；不启动付费运行、不 push、不实施 Day 7）。交付 = `docs/project/research/2026-09-15-full-redesign-options.md`（commit `6ef0c91`，138 行）。零付费、零 GT 读取、零 API 调用、零 DB 写——全部数字来自既有产物（Pilot d / Task 10 三运行 / 账本）。

1. **输入固定**：Task 10 §5/§8 修复后单价（c 集 agent $0.0041 实测 n=1 + sim ~$0.013 估 ≈ $0.017/集）；Pilot 报告 §3.5 终裁（457.6 元 FAIL 2.86×）与 §8/§9 band 纪律（peak=2×，off-peak-only）；账本两节成本；规格 §16.4/§17.3；Day 6 计划 Task 11 三方向定义。
2. **核心增量①（修正建模披露）**：Pilot FAIL 的 sim 侧为 20 集总量平摊（$0.03144/集），对 a-mode 高估 ~8–10×；Task 10 per-call 数据（~$0.001/调用，双源锚定）使按模式建模成为可能——修正后 a-mode sim ~$0.003–0.004/集，「a-mode Full 剩余单项 210 元超线」不再成立（修正后 ~96 元），现状范围总账 457.6 → 基准 201.6 元（1.26×）。**Pilot FAIL 不回溯推翻**（当时数据下的保守裁定）；结构性发现反转如实入账。
3. **核心增量②（方向 0 重推）**：现状范围 + 修复后单价 = 乐观 147.9（余 8%）/ 基准 201.6（1.26× FAIL）/ 保守 298.6 元（1.87×）——策略修复本身砍掉 ~56% 总账（c 澄清 60→12 → sim 调用 -80%）；方法论偏离（情景带代替 bootstrap，n=1/n=0 不足重采样）已标注。
4. **三方向裁定要点**：A 范围压缩 = 唯一两情景均过线的杠杆（A1 c-only 105.7/152.6；A2 200/模式 88.5/129.0；A3 300/模式 116.8/171.4 保守微超；A4 = A3+B 91.6/135.6）；B sim 降本修复后退化为放大器（sim 总盘 86 元，deepseek-flash 或已是价格地板；**合规排查 = 开放项**，Task 1 未覆盖）；C 上限修正 = 只移判据线（260 覆盖基准 / 320 覆盖保守，需修 §16.4 + 总预算两处冻结规格）。
5. **reward 侧裁定**：A/B/C 增益均为 0（成本杠杆不动 SQL 质量）；19/19 集 reward=0，**任何 Full 变体现启即全 0 分**；最便宜信息单价 = 能力验证 2–4 集（~$0.05–0.10，需新授权）。
6. **建议序列（呈报用户，裁定权在用户）**：① 现在不启动任何 Full 变体；② 能力验证（reward>0 门）作 Full 前置；③ PASS 后首选 A4（300/模式分层 + sim 合规排查后启用），全量可比性优先则 C；④ 有界失败 → A1 最小工件或搁置。强制 riders：off-peak-only / 每 25 集重估 / Task 10 三修复项入 Day 7 / 首个 a 集兼作成本验证（超 $0.035/集安全暂停）。
7. **现场**：无代码变更、无 DB 写、无进程残留（本会话零外部状态变更）；PowerContext handoff 见本文件收尾记录与 HANDOFF 头部 revision。

## 19. 能力验证执行：reward>0 未达成，但 c 模式慢性缺陷机制实锤（2026-09-15 晨，付费裁定行使，`task11-capability-validate-20260915a/b`）

**授权链**：用户对 Task 11 裁定清单逐项回复——①批准能力验证（~$0.05–0.10 新授权）②Full 范围按推荐 = A4 ④sim 合规排查按推荐纳入 Day 7（③不适用：A4 无需修 ceiling；⑤余额核对由用户执行，流程已答复）。执行窗：北京周二 07:47–08:50（07:47 起空闲档 ✓；09:00 前完成全部付费调用）。

1. **准备（零付费）**：compose.bird 三服务 + 官方库 `bird_interact_postgresql_full` 起动；**镜像新鲜度容器内实证 ✓**（`_gate_clarification_budget` 2 命中于 `src/commerce_agent/orchestration/bird_server.py`、`prompt-policies-v3` revision 1 命中、三版策略齐备——Task 10 后无代码变更）。选题 `prepare_bird_pilot.py --count 2 --seed 13`（零 API）：`archeology_scan_8`(c) + `archeology_scan_7`(a)，ambiguity 4，与 Pilot 20 + Task 10 M_4 零任务重叠（同库不同题，天然避开同题竞态）；GT 守卫对新 out-dir fail-closed（Task 10 同款坑），`.gitignore` 补 1 行后通过。config-hash = task-selection.json SHA-256 = `0ce49a52…f6fb`。
2. **Run a（experiment `…a`，concurrency=1，c 先 a 后）**：c 集（archeology_scan_8）**15.5s failed，error_class=official_task_error**——2 个单轮会话后编排器报错，属瞬时 infra（栈冷启动）；按 GC7 废弃。a 集（archeology_scan_7）**succeeded 181s：单会话 11 轮，get_schema+知识×5+7 execute_sql+2 ask_user+2 submit_sql，预算 20 内自主提交**——**v3 策略下首个完整 a-mode episode，行为面健康**；两次提交均达评审、Phase 1 failed，reward 0。
3. **Run b（experiment `…b`，c 单集重跑）**：**succeeded**：14 ask_user + 2 submit_sql，两次提交达评审 Phase 1 failed，reward 0。首跑失败确认为瞬时 infra。
4. **判据裁定：reward>0 未达成**（3 有效集 4 次提交全部 Phase 1 failed）。按裁定决策树（有界尝试全 0 → 停止付费）：本轮付费到此为止。
5. **成本**：agent 实测 **$0.034841**（a 0.029314/11 轮 + c 废弃 0.000695/2 轮 + c 重跑 0.013832/16 轮，band 全程 off_peak）；sim 估 ~$0.022（~22 调用）→ 合计估 **~$0.057**（上限内）。账本新增 `task11_capability_validation` 节。
6. **关键发现（本轮最大增量，机制实锤）**：**官方 c 模式每轮新建 agent 会话**（spool 实证：重跑 c 集 16 个会话各恰 1 轮、sequence 均从 0 起；a 模式为单会话连续）——每轮上下文 = phase record。重跑 c 集对话回放：**第 0 轮 agent 持有任务上下文**（问出真实澄清问题「efficiency_status 如何判定」，sim 给出实质回答），**第 1 轮起 sim 转入官方 out-of-scope 拒答、agent 逐轮失忆**（自述「I don't have the original question/schema」）→ 提问漂移 → 占位符 `SELECT 1` 提交 → Phase 1 必败。**回溯实证：Task 10 Run 3「修复后」c 集（12 ask+2 submit）与 Run 2 失控集同样含失忆句式**——Task 5 的闸只治了症状（60→14 ask），未触及机制。**c 模式全部历史 reward=0 的机制 = phase record 未携带任务问题**（我方 adapter 会话状态复现 vs 官方语义，待对冻结官方 allowlist 静态核验定性：是复现缺陷还是官方本就如此而需首轮外记忆策略）。
7. **次要发现**：sim 容器两次 "Could not load schema: All connection attempts failed"（栈冷启动窗口）；sim 第 0 轮仍能实质回答 → schema 加载失败非主因，列为观察项。编排器子进程 stderr 无落盘（可观测性缺口，坑 54 再证：官方 task error 的具体报错不可追溯）。
8. **对 A4 的影响**：预算面不变（c 集 $0.017 锚点仍成立——每轮小上下文与「小轮」成本类吻合）；**能力面：c-mode reward 结构性为 0**，直至 Day 7 完成「静态核验 → 修复 → rebuild + 容器实证 → 1–2 集再验证（新付费授权）」闭环。a-mode 的 Phase 1 失败为真实 SQL 质量问题。
9. **现场收尾**：compose.bird 三服务与官方库 stop（回到开场前）；产品 PG 未动；migration 未动；eval 库新增 3 个 attempt 行（2 实验，证据保留）。未 push。commit：选题产物 `chore` commit + 收尾 docs commit；PowerContext handoff revision 见 HANDOFF 头部。

## 20. Day 7 Phase A：c-mode 判决书 + 修复闭环（零付费，`4287a0b` 同轮）

**授权链**：用户批准 Day 7 计划（「批准执行」）——Phase A 零付费即时实施；C1/C2 付费 Gate 按计划边界行使。

1. **Task 1 判决书**（`docs/project/research/2026-09-15-cmode-phase-record-verdict.md`）：**缺陷 = 我方 adapter**。官方语义（`cinteract.py` run_single_task 104–141 行）：一次 run_session 承载整个 clarify 循环、任务问题在首条消息、`db_schema`/`external_kg` 由官方种子 state 渲染进 instruction、对话靠 ADK 会话记忆累积。我方 `_run_c` 每轮仅以最后一条 sim 回答重建上下文（三者全丢）。**更正**：16 个 spool 文件 = 我方每模型轮新 attempt_id（非官方每轮新会话），机制结论不变。
2. **Task 2 红绿**：RED `test_c_run_phase_context_carries_query_dialogue_and_schema`（修复前第二轮上下文实测 = `'2018\n\n[clarification budget: 1 of 5 …]'`——缺陷赤裸复现）→ GREEN：`_phase_content(task_message, dialogue, state)` 渲染 User Query + `[Task schema]`（state.db_schema）+ `[External knowledge]`（state.external_kg）+ 对话累积（`[agent ask]/[user reply]`）+ Task 5 预算行；`_run_c` 保留首条消息并累积 dialogue。
3. **Task 3**：Ruff 全绿；离线 **867 passed, 140 skipped**（+1）；`bird-system-agent` rebuild + 容器内实证（`_phase_content`×2 / `Clarification dialogue so far` / `Task schema` 命中）✓。
4. **C1 排程**：修复完成于 08:5x，距 peak（09:00）不足以安全完成付费运行——按 off-peak 纪律**排至下个空闲档（12:00–14:00 或晚间）**，一键清单随 HANDOFF §3 移交；预算 ≤$0.05，判据 = 零失忆句式 + 任意一集 reward>0。风险披露：schema 渲染抬高每轮 prompt token，c 集锚或上浮（C1 实测回填）。

## 21. Day 7 Task 4 / Gate C1：reward=0 未过能力门，失败反馈轮残余缺陷精确定位（2026-09-15 午，付费 Gate 行使，`task7-cmode-refit-20260915`）

**授权链**：用户新会话指示「按 HANDOFF §3 清单执行」；C1 ≤$0.05 已随 Day 7 计划授权（HANDOFF §0.2）。执行窗口 12:34–12:44 北京周二（空闲档 12:00–14:00 内完成全部付费调用）。

1. **前置核验**：① `4287a0b` 之后代码路径零变更（`git diff` 空）→ 按清单**跳过 rebuild**；② config-hash 现场复算 = sha256(`outputs/bird-pilot/task11/task-selection.json`) = `0ce49a52…f6fb` 精确一致；③ 官方库就绪探测（pg_isready accepting 后再跑，坑 67）+ 22 官方域库清点完好；④ 容器内实证 `_phase_content`×2、`bird-c-policy-v2` 在场。
2. **运行**：experiment `task7-cmode-refit-20260915`，attempt `a5073903-8076-46f2-ac5d-98b1e0e4a2fa`，c 单集 archeology_scan_8，**succeeded 118.4s**，5 模型轮 = 3 ask_user + 2 submit_sql。
3. **判据结果**：
   - **提交为真实 SQL：PASS**——两次提交均为复杂 CTE + 真实 schema 列（`processing.system_usage` JSONB 键等），**零 `SELECT 1` 占位符**；澄清提问引用 knowledge #37/#51/#17，任务锚定成立。
   - **零失忆句式：FAIL**——首 submit 失败后的反馈轮，agent 如实自述 *"I don't have the original question text in this context — only your note that my previous SQL was incorrect"*（dialogue[4]）；第二次提交改为从 state knowledge 即兴构造（pointcloud SRI），脱离任务。
   - **reward>0：FAIL**（0.0，phase1_passed=false ×2）→ **能力门未过**。按计划 fail 路径：有界诊断 ≤1 轮后停止，交用户裁定。
4. **有界诊断（≤1 轮，零付费）——残余缺陷 = 修复覆盖 clarify 循环、未覆盖 Phase 边界**。官方每 Phase 一次 run_session（判决书 §3/§19）；submit 失败反馈开启**新 Phase** → `_run_c` 再次调用：局部 `dialogue`（bird_server.py:434）清空、`_phase_content` 把 feedback 消息当 "User Query (official first message)" 渲染（bird_server.py:84），而任务问题只存在于首 Phase 的 message 参数、无跨调用持久化。状态级 `self._state["dialogue_history"]` 跨 Phase 存活且 ask_user 后持续维护（bird_server.py:270-273），但 `_run_c` 未用它播种。**修复方向（待裁定后实施）**：首条官方消息持久化入 state + dialogue 自 state.dialogue_history 播种（红绿）→ rebuild + 容器实证 → C1 重验（新付费授权）。
5. **成本**（账本 `task7_c1_refit_20260915` 节）：agent 实测 **$0.014710**（spool 878→883 恰 5 文件，快照 `deepseek-flash-usd-2026-09-12`，全程 off_peak；prompt 65,286 / completion 17,634，reasoning 15,838）；sim 估 ~$0.0015（3 ask_user × $0.0005 精化锚，待用户余额核对）；合计 ~$0.016 ≤ $0.05 ✓。
6. **c 集新锚回填（清单⑤）**：agent **$0.0147/集**——每轮 prompt ~13k（schema+external_kg 渲染），3.6× Task 10 Run 3 锚 $0.0041，§2.24 风险披露实证；sim 调用 60→3（失控期 ~$0.008 → $0.0015）；**合计 ~$0.016/集 vs 旧锚 $0.012**；A4 影响 ≈ +$1.2（288 c 集），可忽略。
7. **现场收尾**：compose 三服务 + 官方库 5433 已 stop，产品 PG 未动；eval 库新增实验 `task7-cmode-refit-20260915`（1 attempt succeeded，证据保留）；spool +5；**残余修复后的 C1 重验属新一次正向运行，需用户新授权**。

## 22. Day 7 残余修复：Phase 边界会话记忆（零付费红绿，2026-09-15 午续，未 commit）

**授权链**：用户裁定「执行① 批准残余修复」（HANDOFF §3 裁定选项 ①；C1 重验 = 新付费授权，修复本身零付费）。

1. **官方语义定案（判决书补充证据，`cinteract.py` 141/159/179 行一手核验）**：整任务**只 init 一次 ADK 会话**——Phase 1 澄清/submit、debug 重试（"Your SQL is not correct. You have one more chance."）、Phase 2 follow-up 全部打同一会话，session 记忆跨 Phase 累积；debug 轮 agent 可见原问题、澄清对话与自己提交过的 SQL（C1 episode 中 agent 索要 "the SQL I submitted last time" 正是官方本可自带的）。
2. **RED**（2 条，`tests/contract/test_bird_system_server_adapter.py`）：`test_c_phase_boundary_keeps_task_query_dialogue_and_submit_memory`（失败反馈轮：断言 debug 轮 phase content 含原问题/对话/首提交 SQL/提交结果/新 orchestrator 消息）+ `test_c_follow_up_phase_keeps_session_memory`（成功后 follow-up 轮同机制——坑 70 边界枚举）。首跑双双 FAIL，失败输出精确复现 C1 实况（debug 消息被渲染为 "User Query (official first message)"，任务问题丢失）。
3. **GREEN**（`bird_server.py` 最小修复面）：`_Session` 持有 `_task_message`（首条官方消息，跨 Phase 持久）与 `_memory`（官方 ADK 会话记忆等价物：ask 对 + submit SQL + 提交结果截断 800 字符）；`_run_c` 首次调用钉住 task_message、dialogue 改引 `self._memory`；`_phase_content` 新增 keyword-only `current_message` 渲染「Orchestrator message for this phase:」（与首条消息相同时跳过）。适配器 **23 passed**（21 存量 + 2 新增）。
4. **终态**：离线 **869 passed, 140 skipped**（+2）；Ruff 全绿；`bird-system-agent` rebuild + 一次性容器内实证（`Orchestrator message for this phase`×1 / `_task_message`×4 / `_memory`×2 命中）✓。
5. **哈希**（HANDOFF §7 已同步）：`bird_server.py` = `9aab08b4…ea26f`；`test_bird_system_server_adapter.py` = `112fb706…a470`。
6. **C1 重验就绪（待用户授权）**：新 experiment `task7-cmode-refit-20260915b`（failed/succeeded 终态不重跑，坑 65 先例）、events 写 `events-c1-refit-b.jsonl`、同 c 单集、同 config-hash、≤$0.05、off-peak；判据不变：零失忆句式 + reward>0。

## 23. Day 7 C1 重验：连续两次 `provider_response_invalid`，修复未被证伪但被 infra 阻塞（2026-09-15 午后，付费 Gate 行使，b/c 两实验）

**授权链**：用户「两者一起授权」（C1 重验 ≤$0.05 + commit）。13:22–13:28 空闲档执行。

1. **入库**：`0f99076`（fix：`_Session` 跨 Phase 记忆 + `current_message`，2 文件 +141/−6）+ `bd38072`（docs：执行日志 §21/§22 + HANDOFF + CLAUDE）。`--check` 披露：HANDOFF 头部 3 行尾随双空格为该文件既有 Markdown 硬换行风格（HEAD 版本本就有 5 行同款），有意保留。
2. **b**（`task7-cmode-refit-20260915b`，attempt `5aac285d`）：failed 97s `official_task_error`；2 模型轮（$0.004848）；agent 容器边界日志：`503 ModelProtocolError reason=provider_response_invalid`。episode 空壳（评测未运行）。
3. **c**（GC7 同授权补跑，attempt `316e3ae4`）：failed 65s，同因 `provider_response_invalid`；1 模型轮（$0.003392）。
4. **诊断（零付费，代码级）**：网关 `provider_response_invalid` = DeepSeek HTTP 响应解析失败（`gateway.py:170-192/217-238` 两个 raise 点：payload/finish/usage 结构，或 `_parse_output` 内 `json.loads(tool_call.arguments)` 截断、模型回显不匹配）。失败响应不留存（Day 3 fail-closed 设计，charge_ambiguous）。**先例：Task 10 Run 1 第 8 轮同 reason（旧镜像）——类别早于本次修复，修复未被证伪**（失败发生在 provider 响应解析，episode 未活到 debug 轮，修复 live 未验证）。疑似触发：长 reasoning 输出下 tool_call arguments 截断。sim "Could not load schema" 每轮一次 = 坑 67 已知观察项（ask 仍 200，非主因）。
5. **成本**：重验授权内 agent 实测 **$0.008240**（b+c，off_peak）≤ $0.05；sim 未及调用。账本 `task7_c1_refit_20260915.reruns` 节。
6. **停止纪律**：连续两次同因 → 非纯瞬时；按计划 fail → 有界诊断 → 停，交用户裁定。精确触发需一次插桩诊断（解析失败时落 sanitized 元数据：finish_reason 字符串 + 异常类 + raise 位置，rebuild 后 1 次付费运行 ~$0.01）。**c-mode 能力门验证状态 = INCONCLUSIVE（被 infra 阻塞，非判据失败）**。

## 24. 插桩诊断定性 + 输出预算修复（2026-09-15 午后，方案 1 行使：诊断付费 $0.008127 + 修复零付费红绿，未 commit）

**授权链**：用户裁定「执行方案1」（插桩诊断 ~$0.01 + 定性；修复红绿零付费随行）。

1. **插桩（红绿）**：`gateway.py` 两个 fail-closed 解析点落 `protocol_diagnostic` sanitized 日志（site / 异常类 / detail / finish_reason 原始串 / 回显匹配 / payload 键集——**零响应内容**）。2 条 RED（payload_parse + output_parse，含 PRIVATE 哨兵零泄漏断言）→ GREEN（`_log_protocol_diagnostic` + `_json_or_none`）。gateway 19/19。
2. **诊断运行 d**（`task7-cmode-refit-20260915d`，attempt `83b6e31d`）：failed 126s；3 模型轮 $0.008127 后第 4 轮命中。**插桩一手证据**：`site=output_parse error=ValidationError`——pydantic `FinalOutput.content` `string_too_short`（空串），`finish_reason='length'`，`model_echo_match=True`。
3. **定性（与两个假设都不同）**：**根因 = 输出预算耗尽，非协议损坏、非重试语义问题**——模型 reasoning 吃满 8192 输出上限 → finish=length + content 空串 → `FinalOutput(content='')` pydantic 校验炸（ValidationError 是 ValueError 子类，被网关吞成不可重试的 `provider_response_invalid`）。晨跑成功轮最大 completion 7133 已贴线；Task 10 Run 1 第 8 轮同解释（a 模式同 8192 上限）。**Day 3 冻结重试语义无需修订**。
4. **修复（零付费红绿）**：`ThinkingConfig.max_output_tokens` le 8192→**16384**（`model/contracts.py`，red：16384 拒绝→16385 仍拒绝）+ `run-profiles.v2.json` bird_a/bird_c 推理 `max_output_tokens: 16384`、profile revision → `bird-a/c-profile-v2`（canonical 形式程序化重写，retail 不动）。配置面调高**不抬正常轮成本**（成功轮本就 <8192），仅把「致命截断轮」变为「更长计费轮」。终态：**873 passed, 140 skipped**；Ruff 全绿；rebuild + 容器实证（`protocol_diagnostic`×4 / `bird-c-profile-v2` / 16384×2）✓。
5. **哈希**：gateway `886c1517…` / model contracts `57849aa6…` / run-profiles `45b3d308…` / test_gateway `c5de626c…` / test_contracts `894df49e…` / test_profiles `4eee27ef…`。
6. **成本**：诊断授权内 $0.008127 ≤ ~$0.01 ✓（重验授权 $0.00824 早前已记）；账本 `reruns.instrumented_diagnostic` 节。
7. **排程**：修复后 C1 重验（experiment `…e`，~$0.015 全程锚）= 下一个付费授权；**14:00 已入 peak，按纪律排晚窗（18:00 后）**。残余风险披露：16384 下 reasoning 仍可能耗尽（概率大降）；若再现，插桩会立即给出同款证据。

## 25. C1 重验 run e：16384 仍被 reasoning 吃满——官方默认 64K 才是水位线（2026-09-15 晚窗，付费行使 $0.011330，停于纪律）

**授权链**：用户「授权执行下一步」（C1 重验，晚窗）。18:04 栈起动 + 就绪探测 + 容器实证（16384×2 / v2 revision 在场）→ 18:05 发射。

1. **运行**：`task7-cmode-refit-20260915e`，attempt `7e6c1ef7`，**failed 196s**；4 模型轮正常（completion 4838/339/5589/7499，reasoning 4637/190/5374/7071，$0.011330），第 5 轮 `finish_reason='length'` + 空 content → 同一 `FinalOutput` 校验炸。
2. **H1（provider 钳制）排除**：① 更名探针双源核验的官方文档（研究笔记 37 行）：`max_tokens` 上限 384K、**思考默认输出 64K（max effort 128K）**——provider 如实接受 16384；② 请求编码路径核验（`gateway.py:358` 直通 + :288 对 393216 校验通过）；③ 容器内配置实证 16384×2。
3. **H2 成立**：该轮 reasoning 真实耗尽 16384、content 零字。**水位线 = 官方思考默认 64K**——官方 ADK 栈不覆写 max_tokens，天然 4× 余量，blowout 罕见；我方任何低于 64K 的上限都让偶发 blowout 保持致命（空 content = 杀整集）。run e 比 d 走得更远（4 轮 vs 2–3 轮）证明 16384 有改善但仍不够。
4. **按坑 73 停止**（不盲试未定性原因）；本授权累计付费 $0.011330。
5. **待用户裁定的修复选项**：① 上限 16384→**65536**（provider 思考默认 = 官方等效，纯配置；blowout 轮最坏 ~$0.04 off-peak，正常轮不变）；② 我方栈优雅处理空 content 轮（run_session 内重问一轮 = 官方 ADK 循环语义；**触及 Day 3 fail-closed 网关契约，用户设计决定**）；③ ①+②。另：确认官方 agent 模型配置是否真的不设 max_tokens 需读 1 个官方文件（allowlist 扩展，待用户确认）。

## 26. 64K 水位线 run f：水位线起效（submit 首次走通），暴露并修复「纯文本轮」缺陷（2026-09-15 晚，付费 $0.018509 + 修复零付费红绿，commit `26047e2`）

**授权链**：用户「按推荐决策执行下一步，并给予授权」（① 65536 + run f，授权口径 ≤$0.05 系列上限）。

1. **红绿入库**：16384→65536（`ce3bdaf`，ThinkingConfig le=65536 + bird_a/c profile revision v3，canonical 重写）+ 插桩入库（`4a1946e`）+ docs（`38ec763`）。873 passed。
2. **run f**（`task7-cmode-refit-20260915f`，attempt `d023d724`，18:21–18:24）：failed 158s，agent **$0.018509**（4 轮：3×tool_calls + 1×stop）。**网关解析失败零命中**（诊断日志 0 条）——前两轮的根因已被水位线修复消除。
3. **水位线起效的一手证据**：clarify 阶段**首次完整走通**——3 轮正常 + submit 达到官方评审（db-env `/submit` 200）；debug 轮模型 reasoning 跑了 **18,297 tokens、completion 18,895**——该轮在 16384 下必死，64K 下正常完成。
4. **新缺陷（我方 adapter）**：debug 轮模型以**纯文本**作答（finish=stop，无工具调用），`bird_c_responder.py:81` 契约守卫抛 `bird_c_candidate_required` → 400 → 杀整集。**官方 ADK 语义：非工具调用响应 = 结束本轮 runner 调用**，编排器按 phase 状态机继续，不是错误。
5. **修复（零付费红绿，`26047e2`）**：`TextCandidate`（type="text"）入 `BirdCCandidate` union；responder 对 FinalOutput 轮返回文本 candidate（替代 raise）；`_run_c` 文本轮 → `[agent note]` 记入会话记忆 + 以该文本结束本轮 run_session（编排器下一条消息继续）。responder 拒绝测试 3 个文本用例转正向（stop/length/content_filter × TextCandidate）+ server 级 run f 场景测试（submit→文本轮→记忆延续→再 submit）。**874 passed**；Ruff 全绿；rebuild + 容器实证 ✓。
6. **停止点**：run g（`…g`，~$0.02–0.05 视 blowout 时机）= 新一次正向运行，按坑 5 等用户授权。

## 27. run g：**首个端到端健康 episode**——判据① PASS（零失忆+真实 SQL），判据② reward=0 未过（真实 SQL 质量）（2026-09-15 晚窗，付费 $0.018851，停于计划 Task 4 纪律）

**授权链**：用户「授权执行下一步」（run g，≤$0.05）。18:39 栈起动 → 容器实证（TextCandidate×2 / 65536×2）→ 18:39 发射。

1. **运行**：`task7-cmode-refit-20260915g`，attempt `accf1991`，**succeeded 161.9s**，4 模型轮 = 2 ask_user + 2 submit_sql，agent **$0.018851**（全程 off_peak）。**边界/诊断日志零命中**——三轮修复后 infra/adapter 层首次全程无错误。
2. **判据①（对话回放零失忆句式 + 提交为真实 SQL）：PASS**——失忆句式扫描全零（run 1 的 *"I don't have the original question text"* 形态消失）；两次提交均为真实 SQL（`mesh` CTE + `mesh_specs`/`system_usage` JSONB 键 + PRU 计算与 CASE 分级）；**debug 轮产生真实修订**（两版 SQL 7 词差异）后重提；第 4 轮 reasoning 15,181 / completion 15,774——在 8192 与 16384 下都会死，64K 水位线下正常完成（修复链价值的直接实测）。
3. **判据②（任意一集 reward>0 = 能力门 PASS）：FAIL**——reward = 0.0；两次提交均未过官方 Phase 1，反馈 = *"SQL failed Phase 1. Test case execution failed."*（隐藏测试用例**执行失败** = SQL 运行期错误，非结果不匹配）。**剩余差距 = 真实 SQL 质量**，与 a-mode 发现同类（执行日志 §19）；进一步定位受 GT 隔离约束（任务 schema 在官方 state 内，agent 可见但未持久化到 agent 可读工件）。
4. **有界诊断结论（≤1 轮，零付费）**：pipeline/adapter/infra 链条已全部健康；能力门差距收敛到单一因素——模型对本题写出可执行且正确的 SQL 的能力。本轮 episode 的澄清预算（max_turn 内 2 次提问）与官方单次 debug 重试均已用尽。
5. **成本与锚点**：run g $0.018851 ≤ $0.05 ✓；c 集锚更新 **~$0.019/集**（agent）+ sim ~$0.002 ≈ **$0.021/集**（64K 水位线让长 reasoning 轮如实计费）；A4 影响 ~+$1.5，可忽略。
6. **停止点**：判据②未达成 = 能力门未 PASS。按计划 Task 4 fail 路径停止，交用户裁定（选项见 §3）。

## 28. run h：异库交叉验证——结构健康跨库成立，reward=0 形态一致（2026-09-15 晚窗，付费 $0.009012，停于计划 Task 4 纪律）

**授权链**：用户「按推荐决策执行下一步，并给予授权」（§3 选项 ①：异题再验一集）。

1. **选题**：`cold_chain_pharma_compliance_3` c（Pilot seed-7 选取，ambiguity 2 最低，异库；**复用 Pilot GT 拆分零新 GT 处理**；config-hash = `b1889777…9017` = Pilot 选取文件 sha256，现场复算精确一致）；单题清单 `outputs/bird-pilot/pilot-task-list-c-refit.jsonl`（公开，待 commit）。
2. **运行**：`task7-cmode-refit-20260915h`，attempt `6332977d`，**succeeded 74.1s**，4 轮 = 2 ask + 2 submit，agent **$0.009012**（off_peak），零边界错误。
3. **结果**：reward = 0.0——但 **sim 真实回应了第一问**（"on-time delivery performance"），两次提交均为 `shipment_overview` JSONB 上的真实 SQL，debug 轮修订后重提。判据①再次 PASS；判据②再次 *"Test case execution failed"*。
4. **跨库结论（两独立题/两库一致）**：结构健康（无失忆/无占位符/无 infra 杀/adapter 零错）已稳定成立；reward=0 形态一致 = **c 模式在冻结工具集下对 JSONB 内键是盲的**（无执行工具）——官方设计预期 agent 用 ask_user 消解此类未知，而 user-sim 的回答能力有限（第二问被拒答）。剩余差距 = c 模式约束下的真实 SQL 质量，与 a-mode 发现同类。
5. **停止点**：按计划 Task 4 fail 路径停止。能力门 reward>0 未达成；后续属策略/质量问题（prompt policy v4 或多集机会），非管线缺陷——管线侧 C1 修复闭环**全部完成并实测起效**。

## 29. Day 7 Task 5+6 零付费收官：四修复项 + A4 清单 + C2 重估表 + sim/a-mode 排查（2026-09-15 晚，commit `89814c5` 等）

用户「按推荐决策执行下一步」（§3 选项 ②+③：Task 6 零付费先行 + A4 批内自然检验能力门）后：

1. **四修复项（红绿，`89814c5`，885 passed）**：① `compose_env_out`——Runner 起 run 时写 `BIRD_EXPERIMENT_ID` compose env 文件（CLI 默认 events 同目录 `compose-experiment.env`，坑 65）；② `RunnerConfig` 校验——同题双模式 + concurrency>1 直接拒绝（坑 63）；③ `_official.py`——子进程 stdout/stderr 以 `communicate()` 捕获并落盘 `{attempt_id}.stdout/stderr.txt`（agent 可见目录），**timeout 路径补 kill 防孤儿编排器继续烧模型 API**（坑 58/68）；④ `scripts/preflight_bird_stack.py`——官方库就绪探测（重试+25 库基线 fail-closed+凭据不回显）+ 实验身份 env 暂存（坑 67/65）。新增测试 11 条。
2. **A4 清单（零付费）**：`prepare_bird_pilot.py --count 600 --seed 20260915 --out-dir outputs/bird-pilot/a4` → **600 集 = 300c+300a，22 库全覆盖（每库 13–14 题）**，与历史选取重叠 30 题（informational）；每模式批量清单派生（`task-list-c/a.jsonl`），同题跨模式永不同批 → 串行纪律天然满足；GT 拆分入 gitignored `a4/task-data/`（.gitignore +1 行）。
3. **Task 5a sim 合规**：compose 拓扑 = 单一 user-simulator 服务服务双模式（**c/a 同配置 by construction**，公平性条款满足）；`USER_SIM_MODEL` env 直通可换（换模 = 一处 env 变更）；官方规程是否允许换模 = 开放项（换前须用户裁定）。
4. **Task 5b a-mode SQL 诊断**（archeology_scan_7，验证日 episode）：探索期 `execute_sql` **全部成功**（JSONB 键正确、schema 在握）——失败主因**不是 schema/执行**：知识定义查询 3 次全失（`ESI` 变体 → "Knowledge not found"）→ agent **自造领域指标公式**；2 次澄清被 sim 拒答 → 提交猜测公式 → Phase 1 失配。**与 c-mode 失败同源 = 领域知识消解缺口（查询 miss + sim 限制），候选 v4 杠杆：知识 miss 时教 agent 向用户索要定义。**
5. **C2 重估表（精化锚）**：c 健康 $0.016/集（run h/g 实测中值+sim）、a $0.025/集、spent 7.93 元 → **基准 ~135 元 vs 160 线 ✓（余 16%）；保守（+40% forward）~186 元 = 超线 16%** → riders：off-peak-only / **c 批先行校准锚，a 批前重估** / 每 25 集重估 / 越线安全暂停 / 能力门如实标注。

## 30. Gate C2 行使：A4 c 批 b01–b04（75 集 succeeded），**余额耗尽安全暂停**（2026-09-15 晚窗 20:01–21:38，agent $1.1155）

**授权链**：用户「批准 C2」（riders 随附）。12×25 集子批结构（每批独立实验身份 `a4-c-20260915-bNN` 便于成本归因与每 25 集重估；每模式批量清单使同题跨模式永不同批）。

1. **b01**（20:01–20:23）：24 succeeded + 1 infra——**stderr 落盘首次实战定性**：官方编排器 Windows GBK 默认编码读数据文件崩溃（`UnicodeDecodeError`）→ 红绿修复 `PYTHONUTF8=1` 注入子进程（`1b1b838`）→ 同实验 resume 重试成功（store 契约语义）→ **25/25**。
2. **b02**（20:27–20:50）：**25/25 零 infra**。**b03**（20:55–21:16）：**25/25 零 infra**。
3. **b04**（21:21–21:37）：21 succeeded + 4 failed（`official_task_error`）——**stderr 实锤 = `ModelBalanceError provider_balance_insufficient`**：DeepSeek 账户余额耗尽，非任务/模型缺陷。
4. **安全暂停（rider 触发）**：立即停止批次循环 + 全栈还原（仅产品 PG 运行）。**75/75 succeeded 集、零失忆、能力门 watch = 0/60 reward>0**（与预 A4 发现一致，批内继续自然检验）。
5. **经济性（spool 实测）**：4 批 93+95+109+93 = 390 turns，**$1.1155 → $0.0116/集**（劣于锚 27%）→ 300 集外推 ~$3.49 agent。今日 agent 侧累计 ~$1.19（C1 系列 0.074 + A4 1.116）≈ 8.4 元；**累计估算 ~15.8 元（待用户余额核对终裁）**。
6. **恢复计划（等充值）**：preflight b05 → b05–b12（200 集）+ 4 集 sweep（solar_panel_3 / robot_fault_prediction_7 / sports_events_19 / virtual_idol_9）→ ~$2.35 agent → c 批重估 → a 批裁定。

## 31. A4 c 批 **COMPLETE**：304/304 succeeded，$0.0113/集（劣于锚 29%），reward 0/300 如实标注（2026-09-15 21:45 → 2026-09-16 00:52 晚窗，agent $3.4567）

**授权链**：用户充值后「已经充值，继续c批吧」。b05–b12 八批 + sweep,每批 preflight（探测+实验 env）→ compose env 重建 agent 容器 → 后台 runner → 聚合入账本。

1. **批次**：b05–b12 每批 **25/25 succeeded 零 infra**（唯一插曲 = b04 的余额耗尽,已由充值解决）；sweep `a4-c-20260915-sweep` 4/4 succeeded（16 turns,$0.057003）。
2. **终账**：**304 episodes（300 主批 + 4 sweep）,agent $3.4567 = $0.0113/集**（vs 重估锚 $0.016,-29%）；turns 总计 ~1,240；全程 off_peak。
3. **能力门终态**：**reward>0 = 0/300**（avg 0.0、phase1_passed 0）——与 run g/h 及 a-mode 诊断的知识缺口结论一致（知识 miss → 自造公式 + sim 拒答）。能力门未过,如实标注；改进杠杆 = prompt policy v4（知识 miss → 向用户索要定义）。
4. **增量重估（c 实测锚）**：spent ≈ 32.4 元（7.93 + c批 24.44）；a 批投影 300 × $0.025 ≈ 53 元；forward（a批+消融+产品）≈ 93 元；**总投影 ≈ 125 元 vs 160 ✓（优于基准 135）**。
5. **现场**：栈与官方库已 stop（仅产品 PG）；eval 库 13 个实验身份记录完整；spool ~1,590 文件；未 push。

## 32. A4 a 批截停收官：223/300 + v4 验证 + 并发升档 2→4（2026-09-16，午窗+晚窗，agent $6.1506）

**授权链**：①「a 批照跑」（12:21）→ ②「授权 v4 验证」（13:5x）→ ③「A」并发升档（21:3x）→ ④ GC7 同因两现自动停（22:45）→ ⑤「②」截停（b10–b12 不跑）。完整细节见 `docs/reports/2026-09-16-a4-a-batch-truncated-and-v4-validation.md`。

1. **b01（午窗）**：25/25 succeeded，$0.0274/集（首个 a 集成本验证 PASS <$0.035）；b02 会越 14:00 peak 线 → 纪律停栈，peak 暂停。
2. **v4 验证**：prompt-policies.v4（bird-a-policy-v3 知识 miss→问用户）零付费实现（888 passed + rebuild 实证）→ 3 集验证 $0.056447：核心 scenario 未被触发（零硬 miss）、广义行为 2/3 激活且 sim 真实作答、reward 0/3 → **回退 v2**（300 集单一策略版优先），v4 归档为将来杠杆。
3. **b02–b07（晚窗，并发 2）**：全 25/25 或 22/22 succeeded 零 infra；b04 出现首个 `ContextBudgetExceeded`（fake_account_24，8 turns 后强制上下文超 64K prompt 预算）。
4. **并发升档**：`RunnerConfig.concurrency` 冻结 le=2（v0.3 §16.2）→ 用户「A」裁定 → le=4 一行 + 钉测试 + 887 passed（宿主侧，零镜像影响）→ b08 试点 **25/25 零 infra、~18 分钟/批（2.3×）**；§16.2 条件升档口的活体证据。
5. **b09 同因两现 → GC7 停**：sports_events_8 同死 `ContextBudgetExceeded`（10 turns）→ 立即停批呈报 → 用户「②」截停：**223/300 succeeded + 2 failed + 75 unrun；reward 0/225；agent $6.1506 = 43.5 元；总投影 ≈116/160 ✓**。
6. **telemetry 导入**：225/225 assigned，0 unassigned/ambiguous（BIRD_EXPERIMENT_ID 接线首战全中）；eval 视图 sum $6.1506 与 spool 精确一致。
7. **现场**：栈与官方库 stop（仅产品 PG）；a 批 10 实验身份完整；commit 待授权。

## 33. 消融 §17.2 COMPLETE：120/120 succeeded，修复价值 = 0（描述性），A 条件省 58–64% 费用（2026-09-17，午窗+晚窗，agent $1.6734）

**授权链**：消融付费门呈批（cap 25 元）→ 用户「授权消融」。条件 A 实现（`e0b3844` adapter 停止门 + `d70b429` compose 接线 + `e87391b` 选取工件，零付费 891 passed）→ B 条件午窗 12:37–13:19 → A 条件晚窗 18:03–18:39（贴 peak 线按 rider 分窗）。细节见 `docs/reports/2026-09-17-ablation-repair-120.md`。

1. **结果**：30 配对题 × c/a × A/B = **120/120 succeeded 零 infra**；P1 两条件 0/60 vs 0/60——**修复救回 0 集**（描述性结论：当前能力下修复价值 = 0）；reward 0/120 如实标注。
2. **经济学**：B 条件 2.4–2.7× 轮次/费用（$1.1885）vs A（$0.4850）零 P1 增益；A 格恰 1.00 submit/集（B 1.87–2.27）= 停止语义活体确认。
3. **执行修正**：60 集混合清单触坑 63 → 拆 c/a 单模式清单串行；B/a 曾用 shell `&` 发射，按坑 58 双时点核验存活。
4. **telemetry 新坑（坑 79）**：c 模式 spool 每模型轮一文件，导入器 1:1 假设对 c 格欠记 ~4×；按 (experiment,task,mode) 预聚合修正后 120/120 精确归属（jsonb 顶层键替换使重跑安全）。
5. **账目**：agent $1.673449 ≈ 11.8 元（cap 25 元内）；栈已 stop；未 push。

## 34. 产品评测 §17.1 COMPLETE：题集从零构建 → A/B → 封闭 10 题；预注册判据否决检索、A 胜出（2026-09-17 晚，agent $0.0595）

**授权链**：范围发现（题集工件从未存在，Task 8 = 构建）→ 呈两路径 → 用户「①」构建 → 40 题人工审核 → 「授权产品评测（cap 5 元）」。细节见 `docs/reports/2026-09-17-product-eval-s17.1.md`。

1. **构建**：题集 v2（50 题双验证：真实库执行 + AST 策略合规，`c52bc67`）+ BM25 路线/zh glossary/harness（`09871dc`）——906→907 passed 零付费。
2. **两次发射失败（$0 成本）全部转化为修复**：① AstPolicy 拒 gold SQL → 揭出**产品真缺陷：布尔运算符被函数白名单误杀，任何 AND/OR 查询不可执行**（红绿修复 + 守卫测试）；② 坑 59 Proactor 复现 → Selector 循环；③ 5 题身份列裸投影违反隐私红线 → 重设计为州/城维度。
3. **A/B×40（v3，$0.0502）**：A 3/40 正确、recall 1.000、tokens 170,696；B 2/40、recall 0.910、tokens 64,046（−62.5%）。**§10.2 判据否决 B（正确率未 +5pp 且 recall 降）→ A 胜出**——小 Schema 下全量上下文更有效（规格预期结论）。
4. **主导失败如实测量**：单步 harness 无 SqlReasoner 修复环，模型默认 SQL ~60% 触发函数白名单拒绝、~26% 未按工具应答；两条件同面，A/B 相对比较成立。
5. **封闭 10 题（A 配置一次，$0.0093）**：1/10 正确。全程 agent $0.0595 ≈ 0.42 元（cap 5 元内）。


## 35. Codex 只读审查回应：23 条抽验落实、6 项代码缺陷修复、数字全面更正（2026-09-18，零付费）

Codex 依交接提示词完成只读审查（`docs/reviews/codex-review-findings.md`）。逐条核实后全部采纳/修复：

1. **数字更正**：c 批「304/304」= sweep 重复计入（DB 305 attempts：300 成功 + 4 failed + 1 infra，300 任务各有成功 attempt）；A4「527/529、0/627」→ **530 attempts / 523 有效评分 / 0 正 reward**；消融轮次比 1.37×（原误写 2.4–2.7×，那是费用比）；「上下文 token −62.5%」→ 总 token（输入 −72.9%）。
2. **评分契约修复（F1/F2）**：Decimal-vs-str 边界 + 逐行单元格乱配 → 4 个真匹配假阴性；契约重写（参考侧类型锚定 + 一致列置换 + 有序/无序分约）→ 保存 SQL 免费重评分：产品评测 A 3→5（12.5%）、B 2→4（10.0%）、封闭 1/10 不变；**裁决不变**。
3. **代码修复（F3–F11）**：Runner 停止后排队任务不再启动（F4）；取消杀死官方子进程（F5）；gather 状态冲突传播而非 exit 0（F6）；SSE 以 run cursor 推进重连位（F3）；导入器唯一候选过时间窗（F9）；harness 移除窄前缀检查（F10）；身份列包装绕过封堵（F7，COUNT 豁免）；窗口/标量子查询不再绕过明细 LIMIT（F11）。回归测试 9 条（tests/unit/review_response/）。
4. **口径纪律**：产品 v2 失败轮 $0.0120 模型费用补记；「88 元」定性为混合口径估算（非平台实扣终账）；「零 peak 泄漏」收窄至 BIRD 轨现存遥测；账本新增 review_corrections_20260918 节。
5. **判定**：919 passed / 140 skipped + Ruff 全绿；审查报告保持未跟踪（Codex 约定），处置记录入本日志与账本。


## 36. Codex 二轮复核处置：8 项验证通过确认、评分器两缺陷重写、嵌套 star 封堵、题集行序契约更正、文档口径落地（2026-09-18，零付费）

Codex 依回复提示词完成独立复核（`docs/reviews/codex-review-verification.md`）：8 项 FIXED-VERIFIED、F2/F7 FIXED-DISPUTED（设计采纳、实现有错）、F8 NOT-FIXED（如实披露）；免费重评分 A 5/40、B 4/40、封闭 1/10 复现。其 4 项 P1 逐项复现核实**全部属实**，处置：

1. **评分器重写（P1-1/P1-2）**：ordered 分支 `pairwise` 误比相邻行（单行必真、正确多行判错、错误单行放行）→ 参考行 i 对实际行 i 逐单元格比较；unordered 分支先按行位置配对再归一化（重排的文本/NULL 结果必错、隐性依赖库行序）→ 两侧按参考列类型独立归一化后再比较（顺序 = 逐位、无序 = 行多重集）；`scoring.py` 尾部死代码清除。
2. **策略硬化（P1-3）**：`_contains_nested_star` 拒绝投影表达式内的嵌套 star（`(s.*)`/`COALESCE(s.*,s.*)`/`COUNT(s.*)`，防复合类型字符串携身份过序列化边界）；`COUNT(*)` 豁免保留，合法裸 star 不受影响；保守误拒（COUNT FILTER 命名身份列、CTE 派生计数别名身份名）按披露接受，不建血缘系统。
3. **题集更正（P2-1/P2-3）**：row_order 逐题声明——dev-02/dev-04/reg-03 明说降序原误标 unordered（新增 `descending` 取值）、reg-02 明说升序；ordered 集 6→10 题；dev-15/reg-03 参考 SQL interval 除法不折算天数 → `EXTRACT(EPOCH FROM (…)) / 86400.0`；builder 升级三层验收（真实库执行 + AST 合规 + **QueryEngine 边界自评分** 50/50）。题面与语义零变化。
4. **免费重评分**（新评分器 + 新元数据重放 v3/closed 保存 SQL）：A 5/7 执行正确（5/40 = 12.5%）、B 4/4（10.0%）、closed 1/1（10.0%）——与一轮更正数字一致；dev-02 在 ordered 契约下仍正确。已披露局限：保存运行仅覆盖 1 道有序题；单行结果值互换与合法列重排不可区分（值匹配契约）。
5. **文档口径落地（P1-4）**：最终报告正文（0/643、12.5%/10.0%、混合口径估算、peak 收窄、932 测试、三层验证）+ README + 面试材料 + HANDOFF 当前态全部改用更正后口径并新增 §8（二轮复核与处置）；历史带日期记录保留原值；HANDOFF §8.14 补 88.1 元来源标签更正标记；账本 budget_restatement 来源更正（7.93 = 7.40 平台核对 + 0.53 agent 估算）；F8 与导入器库级聚合在 HANDOFF §5 落为具体排期项（迁移 + 验收条件）。
6. **测试**：+13 条回归（评分边界 7、AST 嵌套 star 4、F3 同生成器三连拉取 1、F5 fake-process 取消杀子进程 1）→ **932 passed / 140 skipped** + Ruff 全绿。
7. **Day-4 回填披露（P2-4）**：`89390d8` 同时有意补录 Day 4 遗留授权测试 `tests/unit/product_eval/test_day4_authorization.py`（7 条，用户同意保留）——属既有本地测试入库，非本轮新增覆盖。
8. **判定**：零付费、零 GT 读取、产品 PG 只读；复核报告保持未跟踪（Codex 约定）；commit 待授权。


## 37. Codex 三轮验证处置：终局裁断「支持有保留的有限范围收官」；bool 归一化边界修复、文档残余清零（2026-09-18，零付费）

Codex 对二轮处置做独立验证（`docs/reviews/codex-review-verification-r2.md`）：6 项 FIXED-VERIFIED、F8 NOT-FIXED 但延期披露充分、三个设计取舍全部接受；50/50 三层验收与重评分 5/40、4/40、1/10 独立复现（账本 `git ls-files --error-unmatch` 亦确认入库）。处置：

1. **bool 归一化假阳性（新发现 P2-A）**：二轮重写的 `str(bool(value))` 用 Python 真值性作答——`'False'`/`7` 对参考 `True` 判 True。修复为 bool 参考列要求两侧真实 bool（QueryEngine 边界 `_normalize_scalar` 本就保留 PG bool，无字符串化冲突）；True/False×字符串、0/1/数值、NULL 边界用例全部补入（+1 回归）。当前 50 题参考类型仅 Decimal/str/int，无布尔列，已核验数字不受影响（Codex 同结论）。
2. **文档残余（P2-B）**：HANDOFF 标题「11 项 findings 修复」→「10 项修复 + F8 延期披露」；:5/:6 交接状态 ready-for-closure → **closure-supported-with-reservations**；§0 新会话报告口径由 a 批截停态（0/225、75.9、下一步消融/产品/报告）刷新为终态；§3 a 批 reward 0/225 → **0/223 有效评分**（225 attempts/223 succeeded）；产品 $0.06 → **$0.071519040**（v3+closed 0.0595 + v2 补记 0.0120）。最终报告 §8.6 分母 12/80 → 11/80 + 1/10 = 12/90；§8.1 「13 条边界回归」精确化为构成明细。历史带日期记录仍不追溯。
3. **重放脚本退出码（P3）**：`rescore_saved_runs.py` 原把预期内的 A 5/7 也设 exit 1（题目答错与脚本失败混淆）→ 改为报告完成即 exit 0；helper 未跟踪，不影响库结论。
4. **判定**：933 passed / 140 skipped（+1 bool 边界）+ Ruff 全绿；零付费零 GT 读取；验证报告保持未跟踪；commit 待授权。收官口径定格为「有限范围收官 + 已披露保留」（最终报告 §9）。


## 38. 终局裁定：四项全 ACCEPT，签署「有限范围收官声明成立」，审查循环关闭（2026-09-18，零付费）

Codex 终局裁定（`docs/reviews/codex-final-ruling.md`，保持未跟踪，基线 e6053fd）：

1. **增量验证 11 项全 PASS**：bool 严格契约（40 项独立边界断言：ordered/unordered × True/False × 10 种实际值）；文档残余核销（HANDOFF 标题/入口、0/223、$0.071519040、12/90、回归构成）；helper exit 0；保存 SQL 重评分复现 A 5/40、B 4/40、closed 1/10（dev-02 ordered 仍正确）；933 passed / 140 skipped；Ruff 全绿。
2. **四项裁决全部 ACCEPT**：①bool 严格边界正确（不建议增加字符串字面量解析）；②P2-B 清单全部销项；③收官声明（§1 + §3.3 + §9）可以签署——保留五项既定边界（F8 待排期、值匹配契约标签非语义、COUNT 保守误拒、混合口径预算 sim 待核对、有序题仅一道有模型运行覆盖）；④审查循环关闭——无「必须本轮修复」的开放项。
3. **签署语**：**「有限范围收官声明成立（保留已披露边界）；审查循环关闭。」**
4. **记录**：签署入最终报告 §10；HANDOFF 交接状态升级 `closure-signed`（§2.41/§8.18）；四轮累计 findings 11 项 + 后续 P1/P2 全部修复验证或明确接受延期。剩余 = 可选后续（push 授权、仓库补录、F8/导入器库级聚合排期、sim 余额核对），均非收官阻塞。


## 39. 归档与 push：四份审查文件入库、后续安排裁定行使、全历史上 GitHub（2026-09-18，零付费）

Codex 后续安排裁定（push 同意 / 审查文件入库全同意 / Day 1–2 分批补录另开任务 / 两项 P2 排期 / sim 对账优先）行使记录：

1. **归档** `1440185`：四份审查文件按精确路径入库（未跟踪清单核对 + check-ignore 预检，无整目录卷入），文件零改写，四轮基线映射（e73cf3a → 89390d8/32123ef → 5c915ec/cfd77f0 → e6053fd）写入 commit message，`codex-final-ruling.md` 为最终结论。
2. **安排记录** `86061b2`：HANDOFF §5 改写 + CLAUDE.md 待办行。
3. **前置检查四项全绿**：分支 main；远端 origin=github.com/senningi47/commerce-analyst-agent；范围 = 89 commits 全历史（d87768a → 86061b2）；敏感扫描——`.env` 从未入库（全历史无该路径）+ gitignore 生效，树内 secret 形态命中均为测试夹具合成占位（127.0.0.1/generated-N/checkpoint-token），全历史新增行高信号扫描 0 命中。
4. **push**：`git push -u origin main` 成功——远端新建 main 分支、跟踪建立，89 commits 上 GitHub。按项目纪律 push 历来需用户授权；本轮授权链 = 用户委托 Codex 裁定（「我想让Codex做裁定」）→ 用户原样转发裁定 → 裁定条件全部满足后行使，全程留痕。
5. **补录规模发现**：未跟踪盘点显示 Day 1–3 基础层整体未入库（4 个包实现、迁移 0001–0003、alembic.ini、compose.yaml、data/knowledge、~60 测试、bootstrap/import 脚本），远大于此前文档记载；当前远端 checkout 不可运行——分批补录（裁定顺序在 push 之后）为下一步，首批 = 干净 checkout 必需项，逐路径 add、禁止 git add .。


## 40. sim 对账 COMPLETE：平台实扣终账 91.06 元/160（57%），签署边界④解除（2026-09-19，零付费）

用户提供平台读数（后续安排裁定的 sim 对账项行使）：

1. **读数**：总消耗 **91.06 元**；分日 09-15 16:00–24:00 22.36 / 09-16 47.74 / 09-17 12.73 → 16:00 前累计 8.23。
2. **对账**：收官混合估算 88.19 + 已知未计入轮 ≈0.78（f/g/h $0.046372 + v4 $0.056 + 午后诊断 $0.008127，×7.07）→ agent 侧 ≈88.97；**残差 +2.09 元（2.3%）= sim 侧 + 汇率漂移（7.07 指示性）+ 小额取整**；C1 轮 sim ≈ 0.30（16:00 前窗：8.23 vs 7.93）。分窗指示性核对全一致（c 批跨午夜切分为估算，分窗 ±1–2 元摆动，总量权威）。
3. **peak 平台旁证**：四窗全按 off-peak 收敛至 2.3% 残差——隐性 peak（2×）计费必然产生远超此量级的残差，平台账单独立佐证零 peak 泄漏。
4. **终账升级**：**平台实扣 91.06/160（57%，含 sim）**；「混合口径估算」口径退役（假定账户仅用于本项目）；签署保留边界④解除，其余四项（F8/值匹配契约/COUNT 误拒/有序题覆盖）不变。
5. **记录**：账本 `sim_reconciliation_20260919`；最终报告 §11 + §1/§4 预算行升级；HANDOFF §3/§5.3/§8.20；README/面试材料/CLAUDE.md 同步。零付费零补跑；commit 后 push（收官文档流程内）。


## 41. 补录批次 1 COMPLETE：Day 1–3 基础层入库、clone 实测修复两个干净 checkout 缺陷、终验 930/143/0（2026-09-19，零付费）

用户「确认执行剩余事项」行使，按裁定「优先补齐干净 checkout 运行所必需的代码、迁移、配置和测试；不要直接执行 git add .」执行：

1. **入库（4 个逻辑 commit，~110 文件，逐路径 add + 逐文件敏感扫描 0 真实命中）**：`1fe82b1` 迁移 0001–0003 + alembic 三件套 + compose.yaml + .python-version（3.11）；`edf6499` knowledge/model/context_builder/value_resolver 四包实现 + query_engine 内部件 + orchestration `__init__`；`6bfe6bf` configs（tokenizer artifact、capability v1–v3、price 两份、prompt-policies v1、run-profiles v1）+ data/knowledge catalog + 公开/olist manifests + bootstrap/import/provision 脚本 + 两个 manual 向导；`133dbb8` ~55 个测试/夹具（contracts/、fixtures、integration 六目录、unit 四目录）。
2. **clone 实测（裁定核心验收）两轮抓到真缺陷**：首轮 6 failed——①autocrlf 在全新 checkout 把 LF 转 CRLF，破坏快照 evidence 与 Olist manifest 的原始字节 sha256 校验 → 新增 `.gitattributes` 对 configs/**、data/{knowledge,manifests}/**、tests/fixtures/**、docs/project/research/** 冻结换行（`2df0a0b`）；②tokenizer 缓存（6.1MB）系刻意 gitignore 的派生物，clone 缺失 → README 快速开始补一次性免费 provision 步骤（CDN 下载 + fail-closed 校验，无 API key）；③3 个 catalog builder 测试硬依赖 gitignored 的 Olist 原始 CSV → 补 skipif 数据守卫。
3. **终验**：全新 clone + provision = **930 passed / 143 skipped / 0 failed**（= 本地 933/140 − 3 个数据守卫跳过，零失败）；本地 suite 933/140 不变，catalog 测试 3 passed 确认守卫未误伤。
4. **安全处置**：`bird-interact-full-evaluator-only-manifest.json` 按红线（不访问/枚举 evaluator-only 内容）未读取、未入库，留用户裁定（按其向导源码该文件仅含路径/字节数/SHA-256）；`request_bird_full_gt.sh` 审读确认零 GT 内容且内嵌隔离纪律后入库；`scripts/spikes/`、早期 specs/plans/reports、DATA_PROVENANCE 归批次 2；`.vscode/`、`.tmp-*` 建议永不入库。
5. **判定**：零付费零 GT 读取零 DB 变更；远端 `origin/main` @ `2df0a0b` 现可从干净 checkout 运行（含一次免费 provision）。


## 42. 批次 2 裁定：历史材料不执行，补录任务关闭，后续安排裁定全链走完（2026-09-19，零付费）

用户裁定：批次 2 历史材料（specs/plans/research/早期报告/DATA_PROVENANCE/spikes）不执行，维持未跟踪；evaluator-only manifest 同状态（从未读取）；`.vscode/`、`.tmp-*` 永不入库。补录任务就此关闭——批次 1 已达成「干净 checkout 可运行」目标（终验 930/143/0）。

后续安排裁定全链：归档 ✅（1440185）→ push ✅（0f8485a）→ sim 对账 ✅（473d9cf，平台终账 91.06/160）→ 补录批次 1 ✅（1fe82b1…2df0a0b）→ 批次 2 裁定不执行（本节）。交接文档无未决事项；仓库终态 = origin/main 可运行 + 四份审查文件归档 + 平台核对终账 + 全部裁定留痕。
