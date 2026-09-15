# 2026-09-15 c-mode phase-record 判决书（Day 7 Task 1，零付费静态核验）

> 执行：Claude Code（GLM）。官方文件读取范围：`BIRD-Interact-ADK/orchestrator/cinteract.py`（Day 6 Task 1 已确认 allowlist 内同一文件，本轮仅读 run_single_task 会话流）+ 我方自有代码。零付费、零 GT 读取。

## 0. 结论

**缺陷归属 = 我方 adapter（`bird_server.py`），官方语义无缺陷。** 官方 c-interact 的设计是：一次 `run_session` 承载整个 clarify 循环，任务问题在首条消息、schema/知识在官方种子 state、对话靠 ADK 会话记忆累积。我方 `_run_c` 每轮用「最后一条 sim 回答」整体重建上下文，把以上三者全部丢掉。修复 = 按 official 语义恢复三者（commit 见执行日志 §20）。

## 1. 证据链

1. **官方每轮上下文应含什么**（`cinteract.py` run_single_task，104–141 行）：
   - `session_state` 种子键：`task_id / mode / db_name / db_schema / external_kg / max_turn / phase_max_turns / model_turns / tool_trajectory / dialogue_history`（118–129 行）——`db_schema`、`external_kg` 由官方渲染进 agent instruction（c 模式工具只有 ask_user/submit_sql，无 schema 工具，state 是唯一 schema 通道）。
   - Phase 1 消息 = `"User Query:\n{amb_user_query}\n\nYou have {max_turn} clarification turns…"`（135–140 行）——**任务问题在首条消息**。
   - `run_agent_session` **每 Phase 调用一次**（141 行）；clarify 循环在 ADK 会话内进行，**会话记忆跨轮累积**（官方 agent 因此每轮都"记得"问题与对话）。
2. **我方缺陷**（`bird_server.py` `_run_c`，修复前）：
   - `feedback = message` 后，每次 ask_user 后 `feedback = _answer_text(result)`——**任务问题在第 1 轮后丢失**；
   - 每轮 `current_phase = _phase_content(feedback, state)` = 仅最后回答 + 预算行——**无对话累积、无 db_schema/external_kg**（init 原样保存了官方 state，键在场但从未进上下文）。
3. **机制与症状对拍**：验证重跑 c 集（执行日志 §19）第 0 轮 agent 问出任务相关问题（首条消息在场）→ 第 1 轮起只剩 sim 回答 → sim 按官方 out-of-scope 拒答 → agent 失忆螺旋 → 占位符 `SELECT 1`。全历史 c 集（Pilot 9、Run 2/3、验证 2）的失忆句式同源。
4. **对"16 会话"的更正**：16 个 spool 文件 = 我方**每模型轮新 attempt_id**（`_run_c` 内 `attempt_id=self._attempt_ids()` 每轮新取），并非官方每轮新会话——官方每 Phase 一次 `run_session`。机制结论不变，机制描述修正于此。

## 2. 修复（Day 7 Task 2，commit 见执行日志 §20）

`_run_c` 保留首条官方消息 + 累积 `dialogue`（`[agent ask]/[user reply]` 对），`_phase_content(task_message, dialogue, state)` 每轮渲染：User Query + `[Task schema]`（state.db_schema）+ `[External knowledge]`（state.external_kg）+ 对话累积 + Task 5 预算行。RED 测试 `test_c_run_phase_context_carries_query_dialogue_and_schema`（修复前第二轮内容实测 = `'2018\n\n[clarification budget: 1 of 5 …]'`）转绿。

## 3. C1 再验证待证（付费 Gate ≤$0.05，下个空闲档）

- 对话回放零失忆句式、提交为真实 SQL 形态；
- 任意一集 reward>0 = 能力门 PASS。
- 风险披露：schema 渲染会抬高每轮 prompt token（官方语义本就如此；c 集 $0.012 锚或上浮，C1 实测后回填）。
