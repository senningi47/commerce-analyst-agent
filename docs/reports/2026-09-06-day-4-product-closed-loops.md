# Day 4 Product Closed Loops — 最终非付费验证与证据报告

> 执行日期：2026-09-11（Asia/Shanghai）
> 执行者：Claude Code（Opus 5 1M），依据本计划 Task 18 六步执行
> 授权：用户在本会话明确「授权执行 Task18」（新会话独立授权，不继承 09-07 会话）
> 报告性质：只记录实际命令与文件证据；每个数字可回指本报告第 4 节的精确命令或所列文件
> 证据类别标注：**[fixture]** = 确定性离线 fixture/FakeModel 证据；**[PG]** = 真实 PostgreSQL 证据；**[NOT RUN]** = 未运行

---

## 0. 结论速览

```text
Task 13 / Gate M: PASS（源创建，2026-09-06/07 会话）
Task 14 / Gate R: PASS（源创建，2026-09-07 会话）
Task 16 / Gate D: PASS（真实激活，2026-09-07）
Task 17 / Gate W: PASS（真实 seller-risk 写入/读回，2026-09-07；2026-09-11 全量 suite 内复确认）
Task 18:          PASS（本报告，2026-09-11）
Gate P:           NOT REQUIRED / NOT RUN
Gate G:           NOT RUN
Day 4 overall:    PASS
```

三条闭环的证据类别：

| 闭环 | 确定性 [fixture] | 真实 PostgreSQL [PG] |
|---|---|---|
| 卖家风险调查（proposal→approve→execute→readback） | ✅ | ✅（Gate W + 本轮全量 suite） |
| 指标预警（backtest→enable→hit→新调查） | ✅ | 仍为确定性证据 |
| 经营调查（澄清→计划→SQL→对账→报告） | ✅ | 仍为确定性证据 |

另两条闭环在后续真实实例满足全局最终 DoD 前，保持确定性 Product 证据定性，不冒充真实 PostgreSQL 最终实例证明。

---

## 1. 授权与执行范围

- 用户授权：**仅 Task 18**（六步）。Gate P（付费 provider）与 Gate G（Git staging/commit/push）未授权、未执行。
- 过程中的两处用户决定（AskUserQuestion 留痕）：
  1. `tests/integration/checkpoint/` 下 15 个 Day 3 遗留测试与已批准的 Day 4 冻结契约冲突 → **决定：更新测试到 Day 4 契约**（红→绿，owning tests → 完整 gate 重跑）。
  2. `checkpoint` schema 中 42 行 Day 3 残留线程 → **决定：本次不清理，只记录**（见 §15）。

---

## 2. 规格与设计基线（SHA-256，2026-09-11 现场计算）

| 文件 | SHA-256 |
|---|---|
| `docs/project/specs/BIRD-Interact电商经营分析Agent-设计规格-v0.3.md` | `33466c117bc35ac036c334bf2b120bba4071605545d19e09d4f086df78290c81` |
| `docs/project/specs/2026-09-06-day-4-product-closed-loops-design.md` | `d76d260e1dddd070f4f1d89d4d6145db6e9457b71a6a271fe13c5a1cbf842386` |
| `docs/superpowers/plans/2026-09-06-day-4-product-closed-loops.md` | `b63000996fb35ed4d5486eff5319da6cb418fbe68728badf48b76f6e01b9e43b` |

全局 v0.3 规格优先级最高；本 Day 文档与全局规格无已知冲突（本轮未发现需要提交用户仲裁的冲突点）。

---

## 3. 实现与配置基线（SHA-256，2026-09-11 现场计算）

### 3.1 Task 17 交接基线 7 文件（全部与 `HANDOFF.md` §7 一致，7/7 MATCH）

| 文件 | SHA-256 |
|---|---|
| `db/migrations/versions/0004_day4_product_operations.py` | `b1fba3d574c0251848b6778f4a7527a5fda32fbfc44e8a585877cb351967e498` |
| `src/commerce_agent/operations/_postgres.py` | `437bd1efba4f0894f9b9230e84b16da82671682548515100e45b3ea1c3d77782` |
| `src/commerce_agent/operations/_seller_refs.py` | `1060607eba93c11f856eaf235a60eda2e797867f49e3583d55f216f16499e86b` |
| `tests/unit/test_day4_operations_migration.py` | `80b4566ae8fa1b6cb566c72a10bc632c2dbc1749479e188291e8c4e5756df822` |
| `tests/unit/operations/test_postgres_adapter.py` | `4843394b4bd10501214a7ab0fa8c59240b7dd95af891882c985d837d60377208` |
| `tests/unit/operations/test_seller_refs.py` | `f58b4e53ba75b98f19116d8333dbb300f2717396f85be7e282db220b1a62b731` |
| `tests/integration/product_eval/test_seller_risk_postgres_scenario.py` | `d67589266c951d3ce23d97f0a6bdfa56b49e1f5db2b5b2185ebe5292e6de9705` |

### 3.2 Task 18 修改的文件（本轮唯一源码面变更：15 个过期断言更新）

| 文件 | 修改内容 | SHA-256（新） |
|---|---|---|
| `tests/integration/checkpoint/test_retail_postgres_resume.py` | 8 处期望更新（见 §4.3） | `eb509faaadd44854b064a217ff47536f09a733043edcc6401ad6bbe1c88fbb08` |
| `tests/integration/checkpoint/test_checkpoint_privacy.py` | 1 处期望更新（见 §4.3） | `63605191f54b4dfc6a051e070376bd8e741b8574ae51f6b2fa3aa84227aaf18a` |

源码（`src/`）、migration、脚本、配置**零改动**——15 个失败全部被诊断为 Day 3 测试期望过期，而非运行时缺陷（证据链见 §4.3）。

### 3.3 配置与修订标识

| 项 | 修订标识 / SHA-256 |
|---|---|
| `configs/model/run-profiles.v2.json` | `27bb62ff3aa2b36ff619ec6a508ab3297721b797bffeac9cdfbee4cfefb1d80e` |
| `configs/model/prompt-policies.v2.json` | `81c125f959954fd0457ee77433fbdcf6476ce11a999b5c4c7e1a39abb8f06493` |
| `.env.example` | `e4135e23e63905449943e3a08dc5bc978788873af9c8beeea46a06afc35e8c52` |
| `src/commerce_agent/config.py` | `c7cae3c79b80140ee048a5491ebbdd440157814fe77c9122020aa20d9aa64597` |
| Alembic migration 修订 | `0004_day4_product_operations`（head；Task 16 激活，本轮集成 suite 通过依赖其对象存在） |
| Checkpoint 修订 | `RETAIL_STATE_SCHEMA_REVISION="retail-state-v2"`、`RETAIL_NODE_REVISION="retail-nodes-v2"`（`src/commerce_agent/orchestration/_checkpoint.py:18-19`） |
| Profile/fixture 修订 | `run-profiles-v2` / `retail-profile-v2`；fixture `day4-development-v1` / `day4-regression-v1` |

---

## 4. 命令证据（精确命令、数量、时长、状态）

### 4.1 Step 1a — Ruff

```powershell
uv run ruff check src tests scripts db/migrations
```

结果：`All checks passed!`（2026-09-11，PostgreSQL/DeepSeek 开关未设置的干净 shell）。

### 4.2 Step 1b — 完整默认离线 suite

```powershell
uv run pytest -q --tb=line
```

结果：**602 passed, 122 skipped, 1 warning in 12.36s**。

- 122 skipped = PostgreSQL 与 DeepSeek marker 默认跳过（conftest `pytest_collection_modifyitems` 显式注入 skip 理由）。
- 无网络、无数据库变更；默认 suite 运行前后两个开关均 absent。
- 唯一 warning：`tests/unit/scripts/test_provision_deepseek_tokenizer.py` 测试自产 zip 内 `Duplicate name: 'tokenizer.json'`（`zipfile.UserWarning`，测试内部写入行为，非产品缺陷）。
- 历史 `595 passed` 为最终修复前的旧数字，不作为本轮证据（HANDOFF §4.6）。

### 4.3 Step 2 — 完整 PostgreSQL integration suite（含一次失败、诊断与受控修复闭环）

Pre-flight（只读）：

```powershell
docker compose ps                    # commerce_analyst_product_postgres: Up 11 days (healthy)
# 两个开关 present = False
```

首跑（按计划原文命令块，含 try/finally 开关清理）：

```powershell
$env:COMMERCE_AGENT_RUN_POSTGRES_TESTS = '1'
$env:LANGGRAPH_STRICT_MSGPACK = 'true'
uv run --env-file .env pytest tests/integration -m postgres -q --tb=line
```

结果：**15 failed, 106 passed, 1 deselected in 25.58s**。按协议立即停止；失败后现场核验：

```text
11 张 Day 4 表   = 11/11 全部 0 行
application sessions = 0（10 个产品 application_name 的 pg_stat_activity 查询 0 行返回）
两个测试开关     = 已清理（finally + 独立 shell 复核 absent）
```

诊断（只读，`--tb=line` 最小元数据 + 文件阅读，未重跑）：15 个失败全部位于 `tests/integration/checkpoint/` 两个 **Day 3 遗留**测试文件，分两类：

1. **8 个：revision 期望过期**。Day 4 经计划 Task 10（计划第 1508 行）冻结 `retail-state-v2`/`retail-nodes-v2`，且「v1 checkpoints fail with state_schema_mismatch/node_revision_mismatch and are never silently upgraded」。而 `test_retail_postgres_resume.py:346-347` 硬编码期望 v1（6 个参数化用例）、`:644-645` 把 v2 列为「故意非法值」，导致 `:666` 的 2 个「必须抛 CheckpointIncompatible」用例不再抛出（v2 现已是合法值）。
2. **7 个：终局语义过期**。计划 Task 9（计划第 1331 行）冻结 `RetailRunOutcome.status = needs_input | completed | stopped`，raw FinalOutput 不再是合法终局。六段式 legacy 流程按设计 fail-closed（`retail_graph.py:1158-1183` 无条件返回 `stopped` + `typed_retail_terminal_required`）。**决定性证据**：离线测试 `tests/unit/orchestration/test_retail_graph.py::test_retail_graph_completes_one_fake_model_turn`（在本轮 602 全绿 suite 内）以完全相同的图构造断言同一行为（`status == "stopped"`、`stop.reason_code == "typed_retail_terminal_required"`）。

结论：**0 个运行时缺陷**；Day 4 自身全部集成测试（operations ACL/transaction、trace、product_eval、orchestration readback 与 day4 resume equivalence）在首跑中即全部通过。

用户决定①后，按红→绿纪律执行修复（只改测试期望，保留每个测试真正守护的不变量）：

- `:346-347` 期望改为 `retail-state-v2` / `retail-nodes-v2`；
- `:644-645` 非法值改为历史修订 `retail-state-v1` / `retail-nodes-v1`（更强：历史修订必须被拒绝）；
- 7 处终局断言改为 `status == "stopped"` + `stop.reason_code == "typed_retail_terminal_required"`，保留 resume 幂等、预算保留、fingerprint 相等、cleanup 重试恰好一次、隐私不变量等全部机制断言；`RetailRunOutcome` 已无 `final_output` 字段（`contracts.py:217-230`），相应断言随之移除。

Owning tests 重跑：

```powershell
# 开关同上
uv run --env-file .env pytest tests/integration/checkpoint -m postgres -q --tb=line
```

结果：**32 passed in 6.59s**。

Broader gate 重跑（捕获缺陷的完整命令）：

```powershell
# 开关同上
uv run --env-file .env pytest tests/integration -m postgres -q --tb=line
```

结果：**121 passed, 1 deselected in 25.58s**。

运行后现场核验（与首跑后相同的只读查询）：

```text
11 张 Day 4 表 = 11/11 全部 0 行；application sessions = 0；开关已清理
```

### 4.4 Step 3 — 显式 BIRD-isolation gate（离线）

```powershell
uv run pytest tests/unit/orchestration/test_track_isolation.py tests/unit/orchestration/test_bird_a_graph.py tests/unit/orchestration/test_bird_c_responder.py tests/unit/orchestration/test_bird_tools.py -q --tb=line
```

结果：**33 passed in 1.57s**。纯离线；未连接、未枚举、未读取任何 BIRD service/evaluation data。

---

## 5. 三个确定性场景 ID 与断言摘要 [fixture]

来源：`tests/fixtures/product_eval/day4-development.v1.json`（revision `day4-development-v1`）＋执行它们的离线测试（602 全绿 suite 内）。

### 5.1 `seller-risk-investigation-v1` — 卖家风险调查闭环

离线执行：`tests/unit/product_eval/test_day4_scenarios.py::test_seller_risk_scenario_completes_approval_execution_readback`

- 4 个强制澄清槽（`valid_order_statuses`、`time_field`、`minimum_order_count`、`anomaly_threshold`）；禁槽 `business_value` 不得出现。
- 断言：1 条 `open_seller_risk_case` 终态 `succeeded`；读回断言在 `ops_read.investigation_tasks` 与 `ops_read.risk_annotations` 上各 `minimum_rows >= 1` 且列形状精确（`task_ref`/`status`、`seller_ref`/`status`）；audit `operation_executed` 恰 1 条；终局 `completed`。
- 证据 ID：`query:baseline`、`query:seller_drilldown`、`reconciliation:plan`。

### 5.2 `metric-alert-to-investigation-v1` — 指标预警闭环

离线执行：`test_day4_scenarios.py::test_alert_hit_to_task_requires_second_approval`

- 无澄清槽；两次独立审批（alert rule 提案与 alert-hit 调查提案各一次 `approve_execute`）。
- 断言：1 条 `create_and_enable_metric_alert_rule` + 1 条 `create_investigation_from_alert_hit` 均 `succeeded`；读回断言 `enabled_metric_alert_rules`、`enabled_metric_alert_hits`、`investigation_tasks` 各 `minimum_rows >= 1`；audit `operation_executed` 恰 2 条；终局 `completed`。
- 证据 ID：`query:historical_windows`、`query:window_coverage`、`reconciliation:plan`（backtest 仅使用历史窗口，见 §9）。

### 5.3 `ambiguous-gmv-investigation-v1` — 经营调查闭环

离线执行：`test_day4_scenarios.py::test_ambiguous_gmv_scenario_never_infers_required_values`

- 7 个强制澄清槽（含 `gmv_metric_definition=item_amount`、`time_range`、`analysis_grain=month`）；禁槽 `business_value`。
- 断言：1 条 `create_investigation_task` `succeeded`；读回断言 `investigation_tasks` 形状精确；audit 恰 1 条；终局 `completed`；模型从未被允许臆造必填口径值（由 step-scoped 工具表与硬槽校验强制）。

---

## 6. 负向授权矩阵（成功业务写入 = 0）

来源：`tests/fixtures/product_eval/day4-regression.v1.json`（11 个负例/恢复场景）＋ `tests/unit/product_eval/test_day4_authorization.py` 与 `test_day4_recovery.py`（602 全绿 suite 内全部通过）。

### 6.1 零写入负例（8 个场景 × 成功业务写入 0，audit `operation_executed` 恒 0）

| 场景 ID | 期望拒绝 reason_code | 业务写入 | audit |
|---|---|---|---|
| `unapproved-execution-v1` | `approval_required` | 0 | 0 |
| `self-approval-v1` | `self_approval_denied` | 0 | 0 |
| `tampered-grant-v1` | `signature_invalid` | 0 | 0 |
| `expired-proposal-v1` | `proposal_expired` | 0 | 0 |
| `expired-grant-v1` | `grant_expired` | 0 | 0 |
| `replayed-grant-v1` | `idempotent_replay` | 0 | 0 |
| `target-version-conflict-v1` | `target_version_conflict` | 0 | 0 |
| `recovery-before-proposal-persist-v1`（失败注入 `before_proposal_persist`） | 恢复后仍 0 | 0 | 0 |

每个场景的读回断言均为 `ops_read.investigation_tasks` 上 `minimum_rows: 0`（状态 unchanged）。

### 6.2 响应丢失恢复（幂等，业务写入恰好 1）

| 场景 ID | 失败注入 | 恢复语义 | 业务写入 | audit |
|---|---|---|---|---|
| `recovery-before-execute-call-v1` | `before_execute_call` | execute 重试，同一 execution identity | 1 | 1 |
| `recovery-after-execute-commit-v1` | `after_execute_commit_response_lost` | 先 readback 再恢复，**不重复写入** | 1 | 1 |
| `recovery-after-proposal-commit-v1` | `after_proposal_commit_response_lost` | 提案从未提交成功，恢复确认 0 写入（fail-closed） | 0 | 0 |

### 6.3 状态机级负向（离线单测证据）

- 申请/审批分离：`test_workflow_decide.py::test_first_decision_wins_and_self_approval_never_creates_grant`、`test_rejection_is_terminal_and_never_returns_a_grant`
- 幂等：`test_workflow_execute.py::test_execute_retry_returns_original_receipt_without_second_write`
- 提案不可变与版本：`test_workflow_propose.py::test_only_analyst_can_create_one_immutable_proposal`、`test_revising_pending_proposal_supersedes_old_version`、`test_same_idempotency_key_with_changed_payload_fails`
- 失败注入精确匹配 idempotency key：`test_failure.py::test_failure_decorator_matches_exact_idempotency_key_only`、`test_after_commit_decorator_drops_only_first_matching_response`

---

## 7. proposal / decision / execution / nonce / business / audit 状态转移证据 [PG]

真实 PostgreSQL 层（Task 17 创建的集成测试，本轮包含于 121 passed）：

`tests/integration/operations/test_operation_transactions.py`：

- `test_two_concurrent_execute_calls_commit_once` —— 并发 execute 恰好提交一次（确定性 execution_id 主键 + 后到者读回同 proposal 已提交 receipt；`test_postgres_adapter.py::test_execute_once_recovers_receipt_after_concurrent_identity_conflict` 覆盖 23505 路径）。
- `test_two_concurrent_decisions_record_only_one_terminal_decision` —— first-decision-wins。
- `test_target_version_conflict_rolls_back_second_execution` —— optimistic locking。
- `test_constraint_failure_rolls_back_nonce_business_execution_and_audit` —— proposal/decision/nonce/业务/audit 同事务原子回滚。
- `test_post_commit_response_loss_recovers_exact_receipt` —— commit 后响应丢失 = outcome unknown，先 readback 再恢复精确 receipt。

`tests/integration/product_eval/test_seller_risk_postgres_scenario.py::test_seller_risk_write_recovers_and_reads_back_once`（Gate W 正向场景，[PG]）：

- post-commit response loss → outcome unknown → receipt recovery 成功；
- 同一 execution identity 重试只返回同一 receipt；
- investigation、risk annotation、successful execution、audit event 各恰 1 条；
- `ops_read` 可读回 opaque refs；
- **public payload 不含 raw seller ID 或 keyed digest**；
- scoped reset 只清理目标 scenario，sentinel scenario 不受影响；
- finally reset、session leak、开关清理全部生效。

Nonce/grant 生命周期（离线）：`test_approval.py::test_grant_is_256_bit_urlsafe_and_expires_in_exactly_ten_minutes`、`test_grant_expiry_is_inclusive`、`test_grant_changes_fail_closed[payload_sha256/approver_id/proposal_version/key_version]`。数据库只存 grant canonical SHA-256，不存原始 nonce/signature（HANDOFF §2.1；migration `0004`）。

---

## 8. SellerRef 隐私证据与敏感度比较

- 不透明引用：`test_seller_refs.py::test_seller_ref_is_opaque_and_resolves_one_evidence_bound_target` —— SellerRef 是 opaque token，解析绑定单一 evidence-bound target；篡改/证据不匹配/过期全部拒绝（`test_seller_ref_rejects_tamper_evidence_mismatch_and_expiry`）。
- 公共尺度：`test_seller_candidate_normalizes_ratio_to_public_contract_scale` —— 公开 `normalized_value` 量化到 6 位小数（与 backtest 一致；HANDOFF 缺陷 5 的修复）。
- 隐私断言（离线）：`test_privacy.py::test_generated_public_results_have_no_private_runtime_fields` —— 公开结果禁止 `seller_id`、`nonce`、`signature`、`grant`、`authentication_ref` 等私有运行时字段。
- **敏感度比较**：私有身份（raw seller identity 及其 keyed digest）只存在于私有 identity store 的受控边界内；公开 payload、`ops_read` 视图、Trace、audit、checkpoint 与本报告只能出现 opaque SellerRef（HANDOFF 坑 #23）。本轮 allowlist 扫描的 32 位十六进制原始标识形态检查在全部 118 个文件中为 **CLEAN**（§13），Gate W 正向场景断言 public payload 无 raw seller ID/keyed digest（§7）。两个证据类别相互印证：原始身份敏感度最高、仅限私有边界；opaque ref 为公共契约形态。

---

## 9. Backtest：historical-only hit / exclusion / coverage 证据 [fixture]

- 排除规则：`test_backtest.py::test_backtest_marks_low_sample_and_incomplete_windows_as_excluded` —— 低样本量与不完整日历窗口被显式标记为 excluded，不进入 hit 判定。
- 规格哈希绑定：`test_rule_spec_hash_changes_for_every_semantic_input` —— 任何语义输入变化都改变 rule spec hash。
- 变更治理：`test_changed_alert_rule_requires_new_backtest_and_new_approval` —— 规则变更必须新 backtest + 新审批。
- 场景级：`metric-alert-to-investigation-v1` 使用历史窗口回放（evidence `query:historical_windows`、`query:window_coverage`），hit 创建调查必须经过**第二次独立审批**（`test_alert_hit_to_task_requires_second_approval`）。
- 措辞边界：全部证据为 2016–2018 匿名 Olist 历史数据的确定性回放；不构成对当前或未来市场的预测，不用于从相关性推断因果（全局规格非目标）。

---

## 10. SqlReasoner 与 QueryEngine reconciliation 证据 [fixture]

SqlReasoner（生成/修复/no-progress）：

- `test_reasoner.py::test_reasoner_generates_one_bound_candidate_and_reports_usage` —— 生成单个绑定候选并上报用量。
- `test_reasoner.py::test_same_execution_and_same_evidence_stops_without_another_repair` —— 相同 execution fingerprint + 相同证据时停止，不再修复（no-progress 语义）。
- `test_contracts.py::test_sql_reasoning_request_caps_repairs_at_three_and_errors_are_sanitized` —— 修复预算 3 次属于整个 attempt；错误净化。
- 指纹：`test_fingerprints.py::test_literal_change_keeps_structure_and_changes_execution`、`test_identifier_change_changes_both_fingerprints`。
- SqlReasoner 不执行 SQL（计划 Task 7 接口约束；执行只属于 QueryEngine）。

QueryEngine（只读 AST allowlist + reconciliation）：

- 只允许单条有界 SELECT 与非递归只读 CTE；拒绝多语句、写语句（UPDATE/DELETE RETURNING CTE）、SELECT INTO、FOR UPDATE、递归 CTE（`test_ast_policy.py::test_rejects_unsafe_statement_shapes[*]`、`test_rejects_unbounded_or_unknown_joins[*]` 含 LIMIT 1001 无界拒绝）。
- 拒绝未知/特权对象：`pg_catalog`/`information_schema`、缺列、`pg_sleep`、`nextval`（`test_rejects_unknown_or_privileged_objects[*]`）；即使别名化也拒绝直接身份投影（`test_direct_identity_projection_is_rejected_even_when_aliased[*]`）。
- `ops_read` 视图 allowlist：`test_allows_only_reviewed_ops_read_views`；策略拒绝不触达数据库：`test_engine.py::test_policy_rejection_does_not_call_postgres`、`test_ast_attacks_never_reach_executor[*]`。
- Reconciliation：`test_reconciliation.py::test_reconcile_requires_total_and_decimal_rules_to_pass`、`test_reconciliation_detects_invalid_evidence[grain_not_unique/set_mismatch/null_policy_failed]`。

---

## 11. Role / object / function / view ACL 矩阵与安全函数属性 [PG]

证据（本轮 121 passed 内）：`tests/integration/operations/test_operation_acls.py` + `tests/integration/product_eval/test_scenario_reset_security.py` + `tests/integration/orchestration/test_ops_readback.py` + `tests/integration/trace/test_product_trace.py`。

| 登录角色（application_name） | 审定的最小函数族 |
|---|---|
| `agent_reader` | 仅 `ops_read` 视图只读（`test_agent_reader_sees_only_reviewed_ops_read_columns`；`test_agent_reader_cannot_access_raw_operation_audit_or_trace_tables`） |
| `proposal_writer` | proposal 固定函数族 |
| `approval_writer` | approval/nonce 固定函数族 |
| `operation_executor` | execution 固定函数族 + receipt readback |
| `trace_writer` | `app.product_trace_event` 仅追加 |
| `product_scenario_reset` | 仅固定 reset capability（`test_reset_role_has_only_fixed_reset_capability`；`test_reset_removes_only_the_exact_scenario` —— 只删目标 scenario，sentinel 不受影响） |

结构与安全属性：

- `test_owners_are_nologin_and_application_roles_have_no_membership` —— 3 个 NOLOGIN owner 与登录角色零 membership；登录角色 ≠ function owner（HANDOFF 坑 #17）。
- DSN identity 精确绑定在连接前校验：`test_postgres_adapter.py::test_operation_store_rejects_wrong_database_identity_before_connect[role/application_name/database/host/port/search_path]`（proposal/approval/execute 三条 DSN 逐一）+ `test_proposal_read_adapter_rejects_wrong_role_before_connect[...]` + reset adapter 同族（`test_postgres_reset_adapter.py::test_reset_adapter_rejects_wrong_database_identity_before_connect[...]`，含未审定 scope 拒绝）。
- 安全函数属性（Task 16 激活记录，HANDOFF §2.2）：19 个固定 `SECURITY DEFINER` 函数具备固定安全 search_path、schema-qualified SQL、PUBLIC revoke、精确 owner、最小 grant；8/8 execute functions 使用限定 `ops.command_execution AS execution`；`append_audit_event` 无 `FOR UPDATE`（最小 SELECT 权限不含行锁）。
- 四个 `ops_read` security-barrier 视图只暴露 opaque refs 与聚合证据列。

---

## 12. response-loss 恢复、幂等与零 session [PG]

- 恢复分类：commit 后响应丢失 = **outcome unknown**；`test_postgres_adapter.py::test_commit_response_loss_is_outcome_unknown_and_sanitized` + `test_post_commit_response_loss_recovers_exact_receipt`：先 readback，再决定恢复；禁止盲目重复业务写入（HANDOFF 坑 #19）。
- 幂等：`test_workflow_execute.py::test_execute_retry_returns_original_receipt_without_second_write`（离线）+ 并发 23505 恢复路径（在线，§7）。
- 零 session：`tests/integration/conftest.py` 的 autouse 断言对每个 postgres 测试在 finally 后检查 10 个产品 application_name 的 `pg_stat_activity` 计数为 0；本轮两次全量 suite（含失败首跑）后均另行只读复核 = **0**。
- 序列化失败与唯一冲突映射：`test_serialization_failure_is_retryable_known_non_commit`、`test_unique_violation_maps_to_sanitized_non_retryable_conflict`。

---

## 13. Step 4 — Day 4 显式 allowlist 安全扫描

**Allowlist 组成（118 个文件，首遍）**：计划 File Map 全部显式路径 + 12 个 File Map 测试目录的顶层枚举（非递归）+ Task 9 显式列入计划的两份目录外文件（`tests/unit/context_builder/test_profiles.py`、`tests/support/__init__.py`）+ 本轮 Task 18 修改的两个 checkpoint 测试文件 + 两份命名 fixture（`day4-development.v1.json`、`day4-regression.v1.json`）。**排除**：仓库根递归、根 `.env` 及其他 secret 文件、`data/`、outputs、cache、仅评测器可见的受限路径、BIRD service/evaluation data。扫描脚本位于仓库外 TEMP 目录（不新建仓库文件）；敏感模式运行时拼接，命中输出经掩码。

**第二遍（终扫，含本报告）**：119 个文件。终扫前曾出现 4 个报告自命中（本报告引用合成示例的赋值形态 2 处、受限术语直写 2 处），已改为转述后重扫；**终扫 TOTAL HITS = 130，与首遍逐项一致**（§13 下表即终扫数字），0 个真实发现。

**结果（首遍，130 个原始命中 → 逐类裁决后 0 个真实发现）**：

| 检查项 | 原始命中 | 裁决 |
|---|---|---|
| 凭据类字面量赋值 | 3 | 全部是 SellerRef 测试夹具中的合成不透明字面量值（`opaque-token`、`opaque-seller-reference`）——按设计就是公共不透明引用形态，非凭据 |
| 数据库连接串形态 | 29 | 全部为单测中的合成假 DSN（固定测试字符串，`generated-1..N` 占位口令，从不连接）或 provisioning 源的运行时 f-string 构造（无字面量秘密）；计划 Task 14 helper 契约明文允许「unique fixed test strings」 |
| 任意 scheme 带 user:pass 形态 | 28 | 与上一项重叠（同一批合成 DSN/净化错误契约测试） |
| 非对称私钥块 | 0 | CLEAN |
| 32 位十六进制原始标识形态（raw seller identity 形状） | 0 | CLEAN —— 118 文件中无原始身份形态 |
| nonce 字面量赋值 | 1 | 合成哨兵字面量 `not-approved`——负向授权测试中**故意无效**的 nonce 值 |
| signature 字面量赋值 | 0 | CLEAN |
| 私有推理载荷字段 | 9 | 全部是 redaction/访问拒绝守卫自身（`_checkpoint.py:64` 禁键集、trace 脱敏契约、checkpoint/product 隐私测试的哨兵断言） |
| 完整 provider messages 载荷 | 0 | CLEAN |
| 公共契约中的任意写 SQL | 3 | 全部为领域词「grant」（审批授权）造成的词法误报（`contracts.py:306,308`、`errors.py:28` docstring），非 SQL |
| 公共契约中的 URL | 1 | `.env.example` 的 DeepSeek 公共 API 端点（File Map 明文允许的空值凭据名模板，公共端点非秘密） |
| 仅评测器可见术语 | 2 | 两处都位于 `test_privacy.py` fixture 守卫的 forbidden 集内——正是「只允许出现在访问拒绝守卫」要求的状态 |
| fixture 中的最终封闭答案键 | 20 | `gold_tables`/`gold_columns`/`gold_values`（表/列/状态锚点）与 `reference_query_assertions`（`ops_read` 公开视图上的形状断言）——确定性场景验证锚点，非 BIRD 式封闭答案 |
| fixture 中的长 SQL 字面量 | 34 | 全部是 `ops_read` 视图上的读回断言 SELECT 与负例读回（`minimum_rows: 0`），无答案数据 |

辅助守卫（602 suite 内通过）：`test_privacy.py::test_only_approved_fixture_files_have_public_non_secret_content`（fixture 禁止 14 类敏感词，含连接串形态与私有载荷字段名）、`test_contracts.py::test_development_fixture_rejects_final_closed_classification`（fixture 层拒绝最终封闭分类）。

---

## 14. Gate M / R / D / W / P / G 精确状态

| Gate | 状态 | 证据 |
|---|---|---|
| M（migration 源创建） | **PASS** | `0004_day4_product_operations` 源 + `tests/unit/test_day4_operations_migration.py`（无连接导入式测试）；Task 13 会话 |
| R（角色 provisioning 源） | **PASS** | `scripts/provision_day4_operations_env.py`、`scripts/bootstrap_day4_operations.py` + 对应单测（fake cursor/connect，无 DB 变更）；Task 14 会话 |
| D（真实激活） | **PASS** | 2026-09-07：roles/owners 加固、migration 应用、受控 0004→0003→0004 重建（确认 11 表为空后）、live catalog 核验、Gate D 契约 `17 passed in 3.07s`（HANDOFF §2.2） |
| W（真实 seller-risk 写入） | **PASS** | 2026-09-07：security/transaction gate `9 passed in 5.59s` + 正向场景 `1 passed in 2.77s`（HANDOFF §2.3）；2026-09-11 全量集成 suite 内复确认（121 passed 含同一场景与全部 transaction 测试） |
| P（付费 provider） | **NOT REQUIRED / NOT RUN** | Day 4 设计明确以 FakeModel 为准；本轮零 provider 请求 |
| G（Git checkpoint） | **NOT RUN** | 未获授权；仓库保持 unborn `main`、0 commits、文件未跟踪状态；本轮未执行任何 Git 写操作 |

---

## 15. 限制、遗漏与现场状态

1. **checkpoint 残留**：`commerce_analyst` 库 `checkpoint` schema 有 Day 3 时代残留线程（`checkpoints` 42 行、`checkpoint_blobs` 8 行、`checkpoint_writes` 257 行；2026-09-11 只读清点）。与本轮 15 个测试失败无因果（若陈旧 v1 状态命中现行 thread_id，将在 `assert_checkpoint_compatible` 直接抛 `CheckpointIncompatible`，而非返回 stopped——本轮失败形态与之不符）。用户决定**本次不清理**；后续如清理属 DB 写操作，需单独授权。
2. **六段式 legacy 流程**：Day 4 重建后保留的六段式 exchange 流程不再是合法终局路径（raw FinalOutput fail-closed 为 `stopped`/`typed_retail_terminal_required`）。本轮已把 15 个 Day 3 集成测试期望对齐到冻结契约；其守护的不变量（resume 幂等、预算保留、cleanup 重试、checkpoint 隐私）全部保留并有新哈希记录（§3.2）。
3. **另两条闭环的 PG 定性**：指标预警与经营调查目前只有确定性 fixture 证据；按计划要求不冒充真实 PostgreSQL 最终实例证明。
4. **无 Git 基线**：仓库 unborn `main`、0 commits；本报告哈希只证明现场文件状态，不代表已提交。
5. **`.env` 不可人工查看**：本轮仅经 `uv run --env-file .env` 消费（计划原文命令）；DB 核验经容器内 `psql -U commerce_admin` 只读执行，未读取、未打印任何秘密值。
6. **`alembic current` 未在 本轮独立执行**：catalog 处于 0004 head 的结论来自 Task 16 激活记录 + 本轮集成 suite 全部依赖 0004 对象并通过（间接证明）。
7. **默认 suite 的 1 个 warning**：tokenizer 测试自产 zip 的重复文件名 UserWarning，非产品缺陷（§4.2）。
8. **评估器/评测边界**：本报告不包含、不引用、不统计任何仅评测器可见的受限内容或 BIRD 评测内容；相关术语仅以访问拒绝守卫形式存在于测试（§13）。

---

## 16. Overall Decision

**Day 4 overall: PASS。**

判定依据（计划 Task 18 Step 5 完成标准逐条对照）：

- Gate W 真实 PostgreSQL seller-risk 场景通过（Task 17，2026-09-07；2026-09-11 全量 suite 复确认）；
- 完整离线 suite 通过（602 passed，122 默认 skip）；
- 完整 PostgreSQL integration suite 通过（121 passed；首跑 15 个失败已完整诊断、获用户授权、红→绿修复并重跑确认）；
- ACL / 隐私 / BIRD-isolation 全部通过（33 passed 隔离 gate + 121 内的 ACL/隐私/读回/reset 契约）;
- 显式 allowlist 安全扫描两遍完成，0 个真实发现；
- 负向授权矩阵成功业务写入 = 0；响应丢失恢复与幂等证据齐备；11 表 0 行、0 泄漏 session、开关清理核验通过；
- 限制与遗漏显式列出（§15）；确定性 / PostgreSQL / NOT RUN 三类证据在全文分开标注；未从本报告推断任何付费或 Git 动作。

**停在这里。** Gate P（付费 provider）与 Gate G（Git staging/commit/push/clean/reset/checkout）未授权、未执行，等待用户决定。
