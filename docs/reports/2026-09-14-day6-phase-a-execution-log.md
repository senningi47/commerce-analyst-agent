# 2026-09-14 执行日志：Day 6 Phase A 前段（Task 1–3）与授权链

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
