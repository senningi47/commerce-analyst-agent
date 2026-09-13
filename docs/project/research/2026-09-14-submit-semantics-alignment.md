# 2026-09-14 研究笔记：Task 1 提交语义对齐排查（Day 6）

> 排查问题：Pilot d 实验 rewards 全 0 是「提交通道断裂/格式失配」还是「SQL 质量」？
> 方法：只读官方冻结允许清单代码（BIRD-Interact-ADK orchestrator/system_agent/db_environment）+ 我方 bird_system_agent/orchestration 实现 + d 实验 episode tool_trajectory 逐条对拍。零付费、零 GT 数据读取（GT 字段仅存在于官方 --data JSONL，由官方进程消费，本排查未打开任何拆分文件）。
> 官方文件 allowlist（本次只读）：`BIRD-Interact-ADK/orchestrator/runner.py`、`orchestrator/ainteract.py`、`orchestrator/cinteract.py`、`system_agent/tools.py`（submit_sql/ask_user 实现）、`system_agent/agent.py`（instruction）、`system_agent/callbacks_cinteract.py`（引注）、`db_environment/server.py`（/submit 判定，195–240 行）。

## 0. 结论（TL;DR）

**通道无断裂、格式无失配、我方 state 语义与官方一致。rewards 全 0 = 提交的 SQL 全部未通过官方测试用例（结果不匹配，非执行错误）。根因是我方 prompt policy 缺失官方任务策略指令**——Task 5 的修复形状由「prompt 策略整合」确定，无需动任何协议/状态机代码。

## 1. 官方评审链路（证据锚点）

1. **判分在 db-environment 服务**，不在 orchestrator：`POST {db_env}/submit {task_id, sql}` → `{passed, reward, phase_completed, has_follow_up, message, follow_up_query}`（`runner.py:136-150` oracle 模式与 agent 模式走同一端点）。
2. **orchestrator 信任 system-agent 回报的 session state**：`ainteract.py:134-137` 直接读 `state.phase1_completed / state.total_reward`；把 reward 写进 state 的责任在 system-agent 实现。
3. **官方 submit_sql 工具语义**（`system_agent/tools.py:221-268`）：调 `/submit` → 若 `passed` 则 `state.total_reward += reward`、按 `phase_completed` 置 `phase1_completed/current_phase=2` 或 `phase2_completed/task_done`；`_last_submit_raw` 存原始 message（含 `[exec_err_flg]` 调试标记），agent 可见消息剥离该标记。
4. **db_env 判定分支**（`db_environment/server.py:210-218`）：pred SQL 执行错误 → `[exec_err_flg] Error executing…`；超时 → `[exec_err_flg] …timed out`；Query 类测试用例失败 → `Your SQL is not correct.`；自定义 test_cases 分支失败 → `Test case execution failed.`
5. **c-interact 结构**（`cinteract.py:118-160`）：Phase 1 = 一次 `run_session`（内部官方 `_submitted_this_phase` 闸保证每回合恰 1 次 submit）；P1 失败给 **1 次 Debug 修复机会**（按 `[exec_err_flg]` 有无区分「不可执行请修复」vs「不正确再试一次」）；P2 follow-up 在 P1 通过后驱动。
6. **官方 prompt 内嵌任务策略**（`system_agent/agent.py`）：
   - a-interact：**9 工具及 coin 成本逐项列出**（execute_sql=1 / get_schema=1 / submit_sql=3 / ask_user=2 …）+ 策略 tips（先探索 schema/列含义/外部知识、每轮一个澄清问题、节俭预算、**先 execute_sql 验证再 submit**、失败且预算有余就 debug 重试、P2 说明）。
   - c-interact：**「You have at most {max_turn} clarification turns. After that you must submit.」**（max_turn = 歧义数 + patience，由 orchestrator 放入 state）+ 每轮恰一个问题。
   - c-interact 的 `db_schema` / `external_kg` 由 orchestrator 预取放入 session state（`cinteract.py:99-113`）。

## 2. 我方实现对拍（全部一致）

- `src/commerce_agent/orchestration/bird_server.py` `_apply_submit_state`（221–242 行）与官方 `tools.py` 语义逐行等价：`passed` 才加分、phase 置位、`has_follow_up is not True → task_done`（对应官方「有 follow_up 则 phase_transition 否则 task_done」）、`_last_submit_raw` 保存。
- 提交 DTO 精确匹配：`args keys = ['sql']`（官方 `{'task_id','sql'}` 中 task_id 由 port 层携带）。

## 3. Pilot d 实验逐条对拍（11 次 submit）

| 事实 | 证据 |
|---|---|
| 11/11 次提交到达 db_env 并返回结构化判定 | episode `tool_trajectory` 全部含 `{passed, reward, message, …}` 完整响应体 |
| **0 次** `[exec_err_flg]` | 全部 message = `SQL failed Phase 1. Test case execution failed.`（c-mode 唯一一次提交同消息） |
| 我方未误改 reward | `passed=false` → `_apply_submit_state` 不加分——与官方一致，reward=0 是**正确的 state 行为** |
| 样例 SQL（agent 生成，非 GT）语法有效 | fake_account_7 提交的 JSON 路径查询可执行（无执行错误标记），败在结果不匹配（列/过滤语义与预期不符） |

**归属裁定：11 次提交全部为「可执行但结果不匹配」——SQL 质量问题，集成链路无缺陷。**

## 4. 根因：我方 prompt policy 缺失官方任务策略

`configs/model/prompt-policies.v2.json` 现状（Task 1 排查新增发现）：

- `bird-a-policy-v1`：仅 3 句隔离 envelope（"Use an active tool loop until a shared stop primitive fires…"）——**无工具成本清单、无探索/验证/提交策略**。
- `bird-c-policy-v1`：仅 2 句（"return exactly one ask_user or submit_sql call"）——**无 {max_turn} 澄清上限、无「After that you must submit」**。

机制闭环（与 Pilot 报告 §2 证据互证）：

- **c-mode ask_user 539 次**：模型不知有澄清上限，每轮合法地选择 ask_user（bird-c policy 允许 ask_user 或 submit_sql 二选一），60 模型轮顶格 → 8/9 集零提交。
- **a-mode 盲目探索**：模型不知 coin 成本结构（execute_sql=1、get_knowledge_definition=0.5…），83 次 execute_sql + 34 次 get_knowledge_definition 烧尽 18 coin；提交的 SQL 未先 execute_sql 验证（官方 tip 明示该做法）→ 7/9 集提交但全不匹配。
- **模型从未被告知 P1 失败可修复重试**（官方 tip：「If a submission fails and budget remains, debug and try again」）→ exchange_traded_funds_M_1 重提 2 次是偶然行为而非策略。

## 5. Task 5 修复方向（由本排查确定）

1. **prompt-policies v2 → v3（bird-a / bird-c body 整合官方策略）**：保持隔离 envelope 句（不可信任数据声明等）不动，追加官方 a-interact 的工具成本清单 + 策略 tips、官方 c-interact 的澄清上限声明。注意我方架构差异：c-mode 的 max_turn 由我方从 state 读取注入（官方由 orchestrator 填 instruction 占位）；a-mode 官方不预注入 schema（agent 自行探索），保持一致。
2. **c-mode 澄清预算闸（graph 层）**：ask_user 计数达 max_turn 后的模型轮注入策略提醒（对齐官方 before_model_callback「替换该调用结果」语义，携带原 call_id——坑 57），作为 prompt 之外的行为保险。
3. **无需修改**：submit 链路、`_apply_submit_state`、BudgetStopGate、c-mode 每回合恰 1 次 submit 的 `_submitted_this_phase` 闸（均已对齐官方）。
4. **成本影响预估输入（供 Task 11）**：修复后 c-mode 预期轮次 = max_turn + 少量（官方设计 ~3–8 澄清 + 1 submit，远低于 60）；a-mode 预期 coin 用量下降（策略引导下探索收敛），每集 agent+sim 成本同比例下降。

## 6. 遗留与边界

- `Test case execution failed.`（自定义 test_cases 分支）与 `Your SQL is not correct.`（Query 分支）的分布未逐条细分——对修复方向无影响（两者同为结果不匹配）。
- P2 follow-up 从未到达（P1 全败）——修复后若 P1 通过，P2 链路首次被激活，其 state 语义（`current_phase=2`、user_sim `phase_transition`）已在 `_apply_submit_state` 对齐，但属未验证路径，Task 10 小样本验证时关注。
- 官方 `agent.py` instruction 属冻结允许清单文件；Task 5 引用其文本进 prompt-policies v3 时，以「策略要点整合」形式写入我方配置（非逐字复制整个 instruction，保持我方 envelope 结构），并在测试中断言关键策略要素存在。
