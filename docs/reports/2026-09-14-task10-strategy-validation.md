# 2026-09-14 Task 10：小样本策略验证执行报告（Day 6，付费 ≤$0.10 已预授权）

> 执行：Claude Code（GLM）· 2026-09-14 晚（北京 23:08–24:00，空闲档 ✓）
> 输入：执行日志 §16 一键清单 + Day 6 打包授权（决策按推荐行使、付费 Gate、PG 写入、逐 Task commit）
> 全程零 GT 内容读取；GT 拆分文件仅存在性核验；未 push

## 0. 结论（TL;DR）

1. **submit 通道首次真实贯通**：修复后 c 集产出 2 次 `submit_sql`，全部到达官方评审并返回结构化 Phase 1 判定（"SQL failed Phase 1. Test case execution failed."）——Pilot 时期 c-mode 20 集 0 次有效提交的通道断点已消除。判据「c-mode submit_sql ≥1/集」**达成**。
2. **reward 首分 > 0 未达成**：提交的 SQL 未通过 Phase 1 隐藏测试用例（reward 0.0，合法评测结果，非基础设施故障）。Task 10 的通道验证目的达成；SQL 质量属任务难度维度，交 Task 11 重设计。
3. **两个基础设施发现（本报告主要增量）**：
   - ① **同题双模式并发竞态**：a/c 两集并发对同一 task DB（`archeology_scan__archeology_scan_M_4`）做 drop/create，`createdb --template` 500（§2）；
   - ② **容器镜像陈旧**：`bird-system-agent` 镜像烘焙于 2026-09-13（Task 13 期），Task 5 的澄清预算闸与 prompt-policies v3 **从未进入 live 环境**——首次 c 补跑精确复现 Pilot 失控形态（60 ask / 0 submit），重建镜像后即刻收束（§3、§4）。**离线全绿 ≠ 镜像可用**（坑 54 再变体）。
4. **成本**：agent 侧实测 **$0.025047**（3 次有效运行，spool 逐轮聚合）；sim 侧不可见，估 ~$0.05–0.08；合计估 ~$0.075–0.105，处授权上限边界——**本 episode 无论结果如何不再发起付费运行**，权威数字待用户平台余额核对。

## 1. 执行准备（§16 清单第 1–2 步）

| 项 | 结果 |
|---|---|
| 空闲档 | 现场 23:08 北京周一晚 → off_peak ✓（全程 23:13–23:49） |
| spike 容器 6002 | `Exited (0)` ✓（Day 1 遗留，未动） |
| compose.bird 三服务 | 全部起动，端点响应正常；官方库 5433 另行起动（db-env 依赖） |
| config-hash 派生 | 复核确认 Task 13 方式 = **task-selection.json SHA-256**（旧值 `b1889777…9017` 精确复现）；新实验级单值 = `97a40e74…ad749` |
| per-profile 参考指纹 | 现场复算与 §16 记录**精确一致**（bird_a `b4a9505b…`、bird_c `3bfd579a…`）→ 配置面确为 v3 + run-profiles v2 |
| 预算算术 | c1/a1 off-peak 预估 ~$0.057（Task 5 敏感度表 + Pilot a p95），硬线 $0.10 |

## 2. Run 1：a+c 并发（experiment `task10-strategy-validate-20260914`，concurrency=2）

| 集 | 终态 | 详情 |
|---|---|---|
| c | **failed（2.4s，infra）** | db-env `/init_task` 500：`createdb … --template archeology_scan_template` 非零退出。根因 = **同题双模式并发**：两集同时对同一 task DB 名 drop/create（官方 `create_task_db` 命名只含 task_id，不含 mode）。Task 13 两模式异题故从未触发；Task 10 特意选同题对照而撞上。零模型调用、零成本 |
| a | **succeeded（116s）但被 infra 中断** | `final_response = Stopped: infrastructure_error:provider_response_invalid`——第 8 次模型调用被我方网关 fail-closed 拒绝（两分支均为协议校验，非重试类；响应体不入日志，按隐私设计无法进一步定位具体形状）。前 7 轮全部 `status=reported` 正常入 spool：行为形态远好于 Pilot（get_schema → 全列含义 → 知识检索 → information_schema 探索 → 真实聚合 SQL → 2 次 ask_user），coin 13/18 中途。reward 0.0（未及提交） |

**处置**：a 集保留原判（episode 已产出可用轨迹与成本读数，重跑会破 $0.10 硬线；infra-stop 在 final_response 中如实自披露）。c 集按 Day 5 计划 GC7（§8.5.3：基础设施错误 episode 废弃、新 attempt 从头重跑）+ store 契约（failed 为终态不重跑，Task 13 先例：新 experiment 字母后缀）→ **c 单集补跑、concurrency=1**（同题场景消除竞态）。

## 3. Run 2（experiment `…b`）与镜像陈旧发现

c 单集补跑（concurrency=1）succeeded，但 **60 步全部 `ask_user`、0 `submit_sql`、551s**——Pilot 失控形态精确复现；dialogue 120 条零闸拦截；晚期 ask 返回均为 sim 真实回答（非闸提醒）。

**根因（容器内实证）**：`bird-system-agent` 镜像创建于 **2026-09-13 16:17**（Task 13 期最后一次 rebuild），而 Task 5（`ad961e8`，09-14）的 `_gate_clarification_budget` 与 prompt-policies v3 未入镜像——容器内 `grep _gate_clarification_budget` = 0 命中、configs 仅 `prompt-policies.v1.json`。**Run 2 测的是 Pilot 时代代码，不是 Task 5 修复**；修复「无效」的假设被排除，纯属未部署。旁证：a 模式 coin 闸（Task 13 期已在镜像内）在 Run 1 实测正常递减 13/18。

**教训（入 §17/坑 62）**：§16 清单写了「prompt-policies v3 离线全绿 + Task 5 红绿完成」前置，但漏了「**agent 镜像重建**」——凡 agent 侧代码/配置变更后必有 rebuild + 容器内实证（grep 代码/config revision），否则离线全绿只是镜外风景。

## 4. 镜像重建与 Run 3（experiment `…c`）

1. `docker compose -f compose.bird.yaml build bird-system-agent` → up -d → **容器内验证**：gate 代码 2 处命中、`prompt-policies-v3` revision 存在、三版策略文件齐备。
2. c 单集补跑（concurrency=1，23:46 起）：**succeeded，118s**（失控版 551s 的 1/5）。
3. 轨迹：**12 ask_user + 2 submit_sql**；dialogue 24 条（12 ≤ max_turn = n_critical + n_knowledge + patience，闸未需触发、零拦截）；两次提交均到达官方评审并返回结构化判定：`passed=false`，"SQL failed Phase 1. Test case execution failed."，reward 0.0，phase1/2 False。
4. 判读：**行为修复经 live 实证**——探索先行（get_schema/知识/SQL 验证）+ 预算内澄清 + 自主提交，与 Task 1 根因分析（缺官方策略 → 澄清循环失控）的修复形状吻合。reward 0 为 SQL 质量结果（Phase 1 测试用例未过），通道、协议、成本、状态机全链路无故障。

## 5. 成本与账本

| 运行 | experiment | 集 | agent 实测 | 轮次 |
|---|---|---|---|---|
| Run 1 | `…20260914` | a | $0.011539890 | 7 |
| Run 1 | `…20260914` | c | $0（init 即死） | 0 |
| Run 2 | `…b` | c | $0.009389802 | 60 |
| Run 3 | `…c` | c | $0.004117392 | 14 |
| **合计 agent** | | | **$0.025047084** | 81 |

- sim 侧不经过 spool（不可见）：~77 次 sim 调用 × Pilot 均价 ~$0.00098 ≈ **$0.05–0.08**（估）；合计估 **$0.075–0.105**。权威数字 = 用户平台余额核对（Task 13 同款流程）。
- band 全程 `off_peak`、snapshot `deepseek-flash-usd-2026-09-12`、模型回显 `deepseek-flash`——快照与计费链路在真实运行中逐轮验证。
- 账本：`outputs/bird-budget/pilot-ledger.json` 新增 `task10_strategy_validation` 节（gitignored）。

## 6. 遥测导入（Task 2 首次真实使用）

- Run 3 的 14 个 spool 文件（新格式，带 task_id/mode/experiment_id/recorded_at 标记）经 `scan_spool_dir`（14/14 通过，fail-closed 无丢弃）→ `telemetry_patch` → `merge_telemetry` 入 `eval.task_attempt`（14 轮 / $0.004117392 / total 14,759 tokens），评测中心可读。
- **接线缺口（Day 7 修复项）**：agent 容器的 `BIRD_EXPERIMENT_ID` 是 compose 默认值 `bird-system-agent`（每实验 id 未下传容器），导入时做了**确定性改挂**（唯一候选 attempt + 时间窗 + task/mode 匹配，已在账本披露）。修复方向：Runner 起 run 时把实验 id 注入 compose env。
- Run 1/2 的 spool 文件出自旧镜像（无标记），按既有裁定（Pilot d 同款）**不可回填**，其成本以本报告 §5 与账本为准。

## 7. 判据核验表（§16 清单第 5 步）

| 判据 | 结果 |
|---|---|
| c-mode submit_sql ≥1/集 | **✓**（Run 3：2 次，到达官方评审） |
| reward 首分 > 0 即通道证明 | **✗**（reward 0.0：SQL 未过 Phase 1 测试用例——任务难度结果，通道本身已由结构化判定证明贯通） |
| 每集 agent+sim 新成本读数 | ✓ agent 实测 3 集（§5）；sim 待余额核对 |
| 策略修复前后对比（交 Task 11） | ✓ §8 |

## 8. 交 Task 11 的输入（修复前后对比）

| 维度 | Pilot d（修复前，c-mode 9 集） | Run 2（镜像陈旧 = 修复前复现） | Run 3（修复后 live） |
|---|---|---|---|
| ask_user/集 | ~60（539/9） | 60 | **12** |
| submit_sql/集 | ~0.1（1/9） | 0 | **2** |
| wall clock | ~580s（顶格） | 551s | **118s** |
| agent 成本/集 | ~$0.0100 | $0.0094 | **$0.0041** |
| a-mode | 盲探索烧 coin（18 coin ≈ 19 调用耗尽） | — | Run 1：13/18 coin 用于结构化探索 + 2 次澄清（第 8 轮被 provider 协议错误中断，未及提交） |

**Task 11 定价重推要点**：修复后 c 集 ~$0.0041 agent/集（实测，含 12 澄清）+ sim ~$0.013（12 调用）≈ **$0.017/集**，优于 Task 5 敏感度表 N=10 的 $0.0069 估算口径（该口径未含 sim）。a-mode 改善方向被 Run 1 轨迹支持（探索型消费而非盲目烧 coin），但需一次完整 episode 实证。

## 9. 现场（收尾时）

- compose.bird 三服务与官方库（5433）已 stop（回到本会话开场前状态）；spike 容器全程 Exited；产品 PG（5432）未动。
- eval 库：4 个 task10 attempt 行为本次新增（实验数据，保留作证据）；无其他写操作。migration 未动（head 仍 0007）。
- 未 push；commit 见执行日志 §17。

## 10. 遗留与移交

1. **Task 11**（零付费分析）：以本报告 §5/§8 单价重推 §16.4 外推，产出 Full 重设计三方向对比，交用户裁定。
2. **用户动作**：平台余额核对（sim 侧权威数字）；Run 1 `provider_response_invalid` 的具体响应形状如需定位，需网关侧增加一次性 sanitized 形状日志（涉及隐私边界，待裁定）。
3. **Day 7 修复项**：① 同题双模式必须串行（Runner 侧按 task_id 去并发或文档纪律）；② agent 代码/配置变更后的镜像 rebuild + 容器内实证入 preflight 清单；③ `BIRD_EXPERIMENT_ID` 每实验注入 compose env。
4. `cybermarket_pattern_12 [a]` 恢复与 Pilot `crypto_exchange_9` 维持原裁定（不重跑）。
