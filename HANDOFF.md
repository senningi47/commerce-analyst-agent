# CommerceAnalyst 项目交接：**A4 a 批截停 223/300**——v4 验证（reward 中性，回退 v2）+ 并发 2→4（2.3×）+ ContextBudgetExceeded 两现定性；收官路径 = 消融/产品/最终报告

> 更新时间：2026-09-16 23:15（Asia/Shanghai），更新者：Claude Code（GLM）  
> 工作区：`D:\git-projects\commerce-analyst-agent`  
> 当前分支状态：`main`（未 push；a 批全程 + 截停已入账本与报告，commit 待用户授权）  
> 交接状态：`ready-for-closure`——a 批已按用户裁定②截停（223/300 + 2 确定性失败 + 75 unrun），下一步 = 消融/产品 50 题/最终报告与材料  
> PowerContext scope：`git:github.com/senningi47/commerce-analyst-agent`  
> Durable Handoff：PowerContext `handoff/handoff#30`（2026-09-15 提交，exact revision=30；#29 为 Day 7 计划产出轮）

## 0. 新会话先做什么

1. 完整阅读本文件、`docs/reports/2026-09-14-day6-phase-a-execution-log.md`（Day 6 Phase A 执行日志，§30/§31=A4 c 批、§32=A4 a 批截停）与 `docs/reports/2026-09-16-a4-a-batch-truncated-and-v4-validation.md`（a 批收官报告：v4 验证 / 并发升档 / 上下文溢出定性）。把它们当作需要现场核验的历史交接，不要把历史授权当作新会话授权。
2. 先向用户报告准确状态：**A4 a 批按裁定②截停——223/300 succeeded + 2 集确定性 `ContextBudgetExceeded` 失败（fake_account_24 / sports_events_8）+ 75 集未跑（b10–b12）；reward 0/225 如实标注；agent $6.1506 ≈ 43.5 元（telemetry 225/225 精确归属）；spent ≈75.9 元，总投影 ≈116/160 ✓**。v4（知识 miss→问用户）已验证=行为激活但 reward 中性，已回退 v2 并归档。并发 cap 2→4 经用户裁定升档（§16.2 偏差已披露，b08 试点 2.3× 零 infra）。**下一步 = 消融 / 产品 50 题 / 最终报告与材料（见 §5）。**
3. **外部事实（关键）**：模型更名证据链与全部实测数字见研究笔记；价格快照已双源核对（用户读数 = 页面提取）；探针累计花费 ~$0.008。
4. preflight 三项零付费已于 2026-09-13 完成（执行入口备查：`scripts/prepare_bird_pilot.py --dataset <公开数据集路径>`、`--run-db-check`、GT 拒绝检查见执行日志 §4）。Task 13 主运行**需要用户新会话明确授权**（一次正向运行 = 一次授权额度）。
5. 根目录 `.env` 只能由已审核脚本或 `uv run --env-file .env ...` 消费。不要手工读取、打印、搜索、hash 或统计它。（本日已追加 Day 5 变量与 `USER_SIM_MODEL=openai/deepseek-flash`，均经用户授权。）
6. 不要递归扫描仓库，也不要访问、枚举、搜索、索引或统计 evaluator-only / evaluator_only 内容与 BIRD service/evaluation data。只读取本文和报告中明确列出的路径。
7. 不要启动 subagent（除非用户在新会话明确要求 delegation）。
8. `CLAUDE.md` §0 状态行已同步刷新；若仍有冲突，以本文件为准。

## 1. 我们在做什么

项目正在按照以下两份 Day 4 文档实现并验证 Product closed loops：

- `docs/superpowers/plans/2026-09-06-day-4-product-closed-loops.md`（实施计划）
- `docs/project/specs/2026-09-06-day-4-product-closed-loops-design.md`（已批准设计）

全局权威规格是 `docs/project/specs/BIRD-Interact电商经营分析Agent-设计规格-v0.3.md`；冲突时以其为准并停下提交用户审阅。

Day 4 三条闭环：卖家风险调查（proposal→独立审批→受控执行→`ops_read` 读回）、指标预警（backtest→启用→hit→新审批→新调查）、经营调查（澄清→计划→SQL→下钻→对账→报告）。

不可破坏的边界：RetailGraph 只能提议；`OperationWorkflow` 独占 propose/decide/execute 状态机；QueryEngine 独占只读 SQL 与 reconciliation；Product Trace 与 execution audit 分轨；BIRD 与 Product 硬隔离；跨实例恢复只走 PostgreSQL。

## 2. 已经完成了什么

### 2.1 Tasks 1-15 / Gate M / Gate R / Task 15（Codex，2026-08-24 → 09-07）

八种强类型 operation command 与 propose/decide/execute 状态机；canonical JSON；HMAC-SHA256 grant（24h proposal 过期 / 10min grant 过期 / 一次性 nonce / 申请审批人分离 / first-decision-wins / 幂等与乐观锁）；PostgreSQL 只存 grant canonical SHA-256；SellerRef；指标确定性回放；SqlReasoner；QueryEngine reconciliation；四个 `ops_read` security-barrier 视图；RetailGraph v2 终态恢复与 provider-private turn cleanup；ProductScenarioDriver；三条确定性闭环 + 负向授权 + 恢复 + 隔离覆盖；migration `0004` 源；角色 provisioning 源；角色绑定的 PostgreSQL 适配器与集成契约。

### 2.2 Task 16 / Gate D：真实 PostgreSQL activation PASS（Codex，2026-09-07）

创建并加固 Day 4 roles/owners；应用 migration `0004`；确认 11 张表为空后受控 `0004→0003→0004` 重建；live catalog 核验通过；Gate D 契约 `17 passed in 3.07s`。

### 2.3 Task 17 / Gate W：真实 seller-risk transaction/readback PASS（Codex，2026-09-07）

security/transaction gate `9 passed in 5.59s`；唯一正向 seller-risk scenario `1 passed in 2.77s`。覆盖：post-commit response loss 分类为 outcome unknown→receipt recovery→同 identity 重试只返回同一 receipt；investigation/risk annotation/execution/audit 各恰 1 条；public payload 无 raw seller ID 或 digest；scoped reset 不伤 sentinel；finally reset 与零 session。

### 2.4 Task 18：最终非付费验证与证据报告 PASS（Claude Code，2026-09-11）

用户在新会话明确「授权执行Task18」后，六步全部完成：

1. **Step 1**：Ruff 全过；完整默认离线 suite `602 passed, 122 skipped, 1 warning in 12.36s`（PostgreSQL/DeepSeek marker 默认 skip；无网络无 DB 变更）。
2. **Step 2**：完整 `tests/integration -m postgres` 首跑 `15 failed, 106 passed, 1 deselected in 25.58s` → 按协议停止 → 只读诊断（`--tb=line` 最小元数据 + 文件阅读）→ **15/15 全部为 Day 3 遗留测试期望与已批准 Day 4 冻结契约的冲突，0 运行时缺陷**：
   - 8 个：`tests/integration/checkpoint/test_retail_postgres_resume.py:346-347` 硬编码期望 `retail-state-v1`/`retail-nodes-v1`，`:644-645` 把 v2 当「故意非法值」——而计划 Task 10（第 1508 行）已冻结 v2 且要求 v1 必须被拒绝；
   - 7 个：7 处 `status == "completed"` 断言与计划 Task 9（第 1331 行）冻结的终局契约冲突——Day 4 的六段式 legacy 流程对 raw FinalOutput 按设计 fail-closed 为 `stopped` + `typed_retail_terminal_required`（`retail_graph.py:1158-1183`）；决定性反证：离线 `tests/unit/orchestration/test_retail_graph.py:254-257` 以相同图构造主动断言同一行为且在 602 全绿 suite 内通过。
   - 用户决策①：**更新测试到 Day 4 契约**。9 处断言编辑（8+1，只改期望、保留全部机制不变量：resume 幂等、预算保留、fingerprint 相等、cleanup 重试恰好一次、checkpoint 隐私）→ owning tests `32 passed in 6.59s` → 全量重跑 **`121 passed, 1 deselected in 25.58s`**。
   - 用户决策②：checkpoint schema 的 42 行 Day 3 残留线程**不清理，只记录**。
3. **Step 3**：BIRD-isolation 四文件 gate `33 passed in 1.57s`（纯离线）。
4. **Step 4**：Day 4 显式 allowlist 安全扫描（119 文件，两遍）：130 命中逐类裁决后 **0 真实发现**（32-hex 原始身份形态 CLEAN；DSN/凭据类命中全为合成 fixture 与守卫文本；报告自命中已消除）。
5. **Step 5/6**：创建 `docs/reports/2026-09-06-day-4-product-closed-loops.md`（16 节）；机械核对 15 组哈希对全部一致、HANDOFF 7 基线哈希交叉核对通过。**Day 4 overall = PASS**。

本轮唯一源码面变更：两个 checkpoint 测试文件的 15 处过期断言；`src/`、migration、脚本、配置零改动。数据库现场多次只读核验：11 张 Day 4 表 0/11 全空、product application sessions 0、测试开关清理干净；全部 DB 访问只读（`docker compose exec psql -U commerce_admin`），零写操作。

### 2.5 Gate G + checkpoint 清理 + Day 5 计划（Claude Code，2026-09-12）

用户在本会话明确授权三项（Gate G 首个 commit、checkpoint 残留清理 DB 写、Day 5+ 只出计划）：

1. **Gate G PASS**：HANDOFF §7 基线哈希现场复验 11/11 MATCH 后，按计划 G1/G2/G3 执行——`git status --short -- $day4Paths`（计划原文数组 108 条，返回逐条匹配）→ `git add` 后 `git diff --cached --name-only` 与数组 `Compare-Object` 精确一致 → `git diff --cached --check` 干净 → commit **`d87768a51c5e41b864133a2235d42c9e34ede048`**（root-commit，`feat: add day 4 product closed loops`，108 文件 25,111 行）。未 push。`tests/integration/checkpoint/` 两个 Task 18 修改过的文件不在计划 allowlist（计划全文零命中 `integration/checkpoint`），有意保持未跟踪，补录与否待用户决策。
2. **checkpoint 残留清理 DONE**：前清点 42/8/257（与 2026-09-11 一致，4 个 distinct thread）+ `checkpoint_migrations` 10 行（saver 元数据，保留）→ 单事务 `DELETE 257/42/8` → 事务内 0/0/0 → 11 张 Day 4 表 0 行、0 应用会话。
3. **Day 5 计划产出（未实施）**：`docs/superpowers/plans/2026-09-12-day5-bird-contract-and-evaluation-runner.md`——覆盖 v0.3 §14/§15/§16 的 BIRD 契约冻结、`BirdSystemServerAdapter`、生产 `HttpBirdToolPort`、eval schema migration 0005 源、`EvaluationRunner`（Semaphore 2、任务级恢复）、Pilot preflight 与 Gate P 材料（Task 1–12 零付费；Task 13 = Gate P 后程序清单）。执行日志见 `docs/reports/2026-09-12-gate-g-cleanup-day5-plan.md`。
4. **用户决策（同日晚些）**：① Day 5 计划**批准**（计划 Gate 通过）；② checkpoint 两测试文件按助手推荐**补录**——哈希复验 2/2 MATCH 后 `git add` 恰 2 文件 → commit **`2c206acf1f8ff5cd77a11ec0de2c1217a834e365`**（`test: add checkpoint integration tests aligned to day 4 contracts`，2 文件 901 行）。仓库现为 2 commits，未 push。

### 2.6 Day 5 Phase A 实施（Claude Code，2026-09-12 同日稍后，12 个 commit）

用户逐任务授权 commit，TDD 红绿循环完成计划 Task 1–12（`bc5db94` → `3079b60`），终态 **806 passed, 128 skipped, 1 warning**，Ruff 全绿（src/bird_system_agent/tests/scripts/db/migrations 完整命令域）：

1. **Task 1–4**：官方契约 fixture 冻结（`451fe2c` 机械提取 + anti-leak 断言）→ `BirdToolPort` 协议放宽 → `HttpBirdToolPort`（§14.2 重试矩阵）→ `BirdSystemServerAdapter`（官方会话状态键全复刻、c 每轮恰 1 submit、`BirdSessionStatePort` 预算门装饰器）。
2. **Task 5–6**：纯 ASGI system agent 服务 + 生产工厂 + allowlist 构建上下文 + `compose.bird.yaml` 三服务拓扑 + 9 条 GT 隔离断言（agent 零挂载、数据集不进任何容器）。
3. **Task 7–9**：eval schema migration `0005` **源**（未应用）+ 角色 provisioning 源；评测契约/守卫式内存与 PG store（failed 不重跑语义）；agent spool 校验/只读归档 + `EpisodeExecutor` 接缝。
4. **Task 10–12**：可恢复 `EvaluationRunner`（gather-vs-stop 竞速修复竞态）+ 原子 `finish_attempt(result=)` 单事务 + CLI + 官方 orchestrator 子进程执行器 + 状态机 6×6 矩阵（**抓到终态重跑漏洞并修复**）+ live PG 契约测试（默认 skip）+ Pilot 预检脚本与 **Gate P 授权请求**（`docs/project/specs/2026-09-12-day5-gate-p-pilot-authorization-request.md`，建议上限 30 元待用户裁定）。

关键一手发现与 preflight 5 项待办：见 `docs/reports/2026-09-12-day5-phase-a-implementation.md`（含全部 commit ID 与逐任务 suite 增量）。

### 2.7 Gate P 裁定与 preflight 执行（Claude Code，2026-09-12 深夜会话，6 个 commit）

用户裁定 Gate P：**上限 30 元**，熔断双条款按文档，preflight+Pilot 一并授权（逐项核验、失败即停），commit 逐项授权。执行结果（细节与授权链见 `docs/reports/2026-09-12-gate-p-preflight-execution-log.md`）：

1. **开场差异**：`ed4aa60` 漏提交的测试修复入库 `b6451d6`（HEAD 恢复与 806 数字一致）。
2. **① coin 停止桥接**（`7553337`）：`BirdARunRequest` 上限 le=60；`BirdAModelTurnGate` + `_BudgetStopGate` 补齐官方 before_model_callback 的 task_done/budget<0 两道闸；adapter 传 60/60。811 passed。
3. **⑤ spool 写入接线**（`2938b91`）：`SpoolTraceGateway` 装饰器 + compose 唯一 agent 卷 `./outputs/bird-agent-spool:/app/spool` + 隔离测试演进。**导入侧延后**（run_scope_digest 传输方案未定，Day 6）。
4. **② digest/requirements/build**（`1214383`）：基础镜像经 daemon 镜像源拉取并固化 linux/amd64 digest；uvicorn 锁 0.52.4；.env 补齐 Day 5 变量（含官方栈默认凭据 root/123123、sim 模型 DeepSeek 同款）；三镜像 compose build 成功。
5. **③ 0005 + 角色 + live**（`fd79fa6`）：0005 应用、evaluation_owner/writer 建立并加固；live 首跑 5 failed 抓到两个真实缺陷（`_telemetry_json` 裸 dict 绑定 → Jsonb 修复；live harness 缺 experiment 注册 → fixture 补齐）——**坑 2 的精确复现**。终态 live **127 passed**。
6. **④ 探针 + 模型更名适配**（`a2ef27f`）：**DeepSeek 于 2026-09-10 退役 `deepseek-v4-flash`（由 DeepSeek-V4.1-Flash 服务、Flash 价格计费）**，响应回显改 `deepseek-flash` → 我方网关 fail-closed 拒绝（行为正确）。已双源核对价格（用户读数=页面提取）并重建 capability/price 快照 v4、切换全部配置面。能力字段全部实测验证（回显/tool-calling/usage/thinking/成本核算）。**探针门判据（Day 3 固定 SQL 链）确认与 Day 4/5 架构不兼容 → 门 FAIL 状态如实保留，标 PASS-with-documented-limitation**；探针累计花费 ~$0.008。新增技术债三项（探针重设计、v2 decide 工具名单与图白名单不一致、spool 导入侧）见执行日志 §6.5。
7. 本会话还发现并修复工作区遗留：`ed4aa60` 漏提交的测试对齐（见第 1 条）。

终态证据：离线 **815 passed / 128 skipped**；live `-m postgres` **127 passed**；Ruff 全绿；三镜像已 build。

### 2.8 Preflight 零付费收尾（Claude Code，2026-09-13）

用户在新会话指示「继续 preflight 零付费项」后，HANDOFF §5.1 剩余三项全部完成（细节与授权链见 `docs/reports/2026-09-13-preflight-zero-cost-completion.md`）：

1. **① 20 题选取 PASS**：seed 7，c=10 / a=10，10 库覆盖，strategy=`database+ambiguity-fallback`（§16.3 fallback，官方 manifest 无 BI/DM 标签）。产出 `outputs/bird-pilot/task-selection.json`（公开，零 GT token）、20 个 GT 拆分（gitignored `task-data/`）、`outputs/bird-budget/pilot-ledger.json` 账本种子。
2. **② 官方 db-check PASS**：`returncode 0`，22 库/244 表/2,011 列/273,571 行与 Day 1E 基线**全一致**——官方 BIRD 库 13 天连跑零漂移。
3. **③ GT 路径拒绝检查 PASS**：一次性 agent 容器（`compose run --rm --no-deps`）内 GT 路径与 db-env 公共数据挂载点均 `FileNotFoundError`，env 零 GT/凭据变量名；宿主 GT 目录存在对照成立；结构前提由 9 条契约断言锁定。
4. **途中红绿修复 `prepare_bird_pilot.py` 三个缺陷**：checker 子进程极简 env 破坏 Windows 临时目录解析（`_checker_env()` 运行时白名单，秘密仍不进子进程）；默认 `--adk-root` 指向不存在路径（实际 checker 在 `_upstream/BIRD-Interact/env/`）；基线解析正则被输出头部 `127.0.0.1` 假阳性（锚定 `Total X:` 汇总行；单测合成输出换为真实一手输出）。终态 **818 passed, 128 skipped**（815→818），Ruff 全绿。
5. 本轮零付费 API、零 GT 内容读取（容器检查零字节读取）、未 commit 未 push（待用户授权）。

**遗留提醒：`commerce_analyst_bird_db_spike`（Day 1 遗留）占 127.0.0.1:6002，与 compose `bird-db-environment` 端口冲突——Task 13 起真实栈前必须处置。**

### 2.9 Task 13 主运行与六个活体缺陷（Claude Code，2026-09-13 本会话）

用户开场授权「下一步」后：commit `4ff1195`（6 文件）→ .env 追加 15 个 orchestrator 变量（3 个未知值经官方 shared/config.py AST 提取；LITELLM_API_KEY=${DEEPSEEK_API_KEY} 引用）→ `docker stop` spike 容器 → task-list 派生 → compose 栈起动即暴露**六个活体缺陷**（全部红绿修复，详见执行日志 §2）：①agent 镜像 requirements 重冻结 45 包 ②user-sim 全钉 + sqlglot ③快照评审格式加载缝上移 `src/commerce_agent/model/snapshots.py` ④契约 fixture 入镜像 ⑤provider_user_id fail-fast + 32 零默认 ⑥bird_a 官方工具目录 + 预算门身份保持；另加 server 边界消毒日志。四轮尝试史（a/b/c/d）与两个付费诊断集见执行日志 §3。**终态：d 实验 18 succeeded + 1 failed + 1 unfinished；agent 侧 1.4932 元/30 元上限；离线 829 passed；Ruff 全绿。** 账本已写 `outputs/bird-budget/pilot-ledger.json`。spike 容器已 stop。

### 2.10 Pilot 报告期（Claude Code，2026-09-13 报告会话，零付费）

用户指示「阅读HANDOFF.md，开始项目推进」后，按 §5.2 完成 Pilot 报告（`docs/reports/2026-09-13-task13-pilot-report.md`，零付费、零 GT 读取、未 commit）：

1. **三源对账**：spool 785 文件逐行解析（12 个多行 JSONL 是关键——按文件单对象解析会漏 84 turn）得 883 turn；d 窗口（mtime ≥ 08:20:59.687Z）675 turn / **$0.211203** / prompt 1,747,080 / total 2,000,627 与执行日志**精确一致**；`eval.task_attempt` 只读查询 20 attempt 终态一致。
2. **新披露——诊断期成本**：d 窗口之前另有 208 turn / $0.069457（c 轮实验 $0.008 + 付费诊断 ~$0.0615）——诊断授权上限 $0.05，实测超 ~$0.012，属追溯计量披露。诚实口径已实际花费 $0.2807 ≈ 1.98 元。
3. **§16.4 外推**：分层 bootstrap（seed 7，N=20000）——c 集 = 60 small turn、a 集 = 9 small + 9 big；结构配平 c 8×60 + 33(failed) + a 9×18 = 675 turn 与实测精确相等。Full 剩余 1180 集 p95 $17.38、产品 90 集 + 消融 120 集 p95 $2.73；**合计 p95 144.2 元 ≤ 160 元预算线（agent 侧口径，simulator 侧 invisible 待用户余额核对终裁）**。账本 JSON 已更新（gitignored）。
4. **rewards 全 0 机制（episode tool_trajectory 聚合，报告 §2）**：c-mode 9 集 `ask_user` 539 次 + `submit_sql` 仅 1 次（澄清循环失控）；a-mode 7/9 集发生 `submit_sql`（共 10 次）但 SQL 未过评审，bird-coin = 工具调用次数（18 coin ≈ 19 次调用耗尽）。**结论：策略层系统性问题，非基础设施故障；待排查提交语义对齐（替代假设）。**
5. **Full 启动建议：暂缓**——预算门有条件通过、能力门不通过；前置：提交语义排查（零付费）→ c/a 策略修复 → 2–4 集小样本重跑（需授权）。

**本轮改动**：新建上述报告、更新 `outputs/bird-budget/pilot-ledger.json`（gitignored）、本文件刷新。零源码改动、零付费调用、零 GT 读取。commit 待用户授权。

**同会话终裁追加（余额核对闭环）**：用户报来 09-13 平台 deepseek-flash 消费 **6.43 元** → 拆分 sim 侧 $0.6288（4.45 元，agent 的 2.24×；band 疑点查证排除——周日运行 off_peak 正确，`_pricing.py:46` weekday 门 + 快照 evidence "weekdays"）→ 计入后 §16.4 总账 **457.6 元 vs 160 元线 = FAIL（2.86×）**；结构性发现：a-mode Full 剩余单项（agent p95 + sim）= 210 元即超线，Full 全量 1200 集数学上不可行。**预算门终裁 FAIL（与能力门独立成立）→ 综合裁定 Full 不启动**；报告 §0/§3.5/§4/§5/§7 与账本（`balance_cross_check`、`projected_total_upper_bound_yuan=457.6`、`budget_gate_decision=FAIL`）已同步，HANDOFF §0/§3/§5 已刷新。

**Band 澄清（2026-09-14 续会话，两轮，最终见 Pilot 报告 §9 与执行日志 §9）**：用户先称「6.43 元为高峰计费」→ 引发一轮对账修正（agent 3.74 / sim 2.69、0.72×、两档外推）；随后用户提供**官方峰值窗口定义**（北京周一至五 09:00–12:00、14:00–18:00，其余空闲）并撤回该说法（运行当天是周日）→ **§3.5 原对账全部恢复有效**（sim $0.6288 / 2.24× / 457.6 元 FAIL / a-mode 单项 210 元超线）。**band 模型经官方定义精确确认**：快照 `peak_windows_utc` + evidence「weekdays」+ `_pricing.py` weekday 门与平台定义完全一致——无缺陷、无需重冻结。新增**运行纪律**：peak 档恰为 off-peak 的 2×，付费运行（Task 10、未来 Full）一律调度空闲档，否则剩余翻倍（~908.8 元 / 5.68×）；Pilot（周日）已天然满足。中间态修正（commit `211648a`）保留为审计痕迹。

### 2.11 Day 6 计划 Gate（Claude Code，2026-09-14 会话）

用户双重授权（「授权进行下一步并批准启动Day6计划」）后：commit `3a71357`（Pilot 报告终裁 + HANDOFF）；产出 Day 6 计划（11 Task 三线：能力门修复 / 技术债三项 / Day 6 规格 DoD；付费 Gate 仅 Task 3 ≤$0.01 与 Task 10 ≤$0.10；Global Constraints 继承 Day 5 十一条 + 新增冻结契约不可动、UI 收窄、两门 FAIL 为输入基线）；现状确认 `api/`、`web/`、`tests/e2e/` 全新范围 + 官方 `init_session` 契约含 `session_id`。随后用户批准计划并打包授权（见 §2.12）。

### 2.12 Day 6 Phase A 前段：Task 1–3（Claude Code，2026-09-14 会话）

用户打包授权（计划批准 + 逐 Task commit + 付费 Gate + PG 写入预授权）后，执行至上下文收尾阈值（细节见 `docs/reports/2026-09-14-day6-phase-a-execution-log.md`）：

1. **计划入库** `b1c9a4e`。
2. **Task 1**（`1bdcf78`）：提交语义排查——评审在 db_env `/submit`；11 次 submit 全部到达、0 次 exec_err_flg（全部「可执行但结果不匹配」）；**根因 = bird-a/bird-c prompt policy 缺官方策略**（成本清单/探索 tips/澄清上限）。Task 5 形状：prompt-policies v3 + c-mode 澄清预算闸。
3. **Task 2**（`4ceab5c`）：spool 导入接线——`spool_importer.py`（scan/assign/patch，(experiment,task,mode)+时间窗 join，歧义 fail-closed）；agent 事件加 task_id/mode/experiment_id/recorded_at；`AttemptTelemetry` +agent_turns/agent_session_id；`merge_telemetry`（PG jsonb 合并 + 内存实现）。**d 旧格式 785 文件不可回填**（聚合已在账本），回填对新 run 生效。
4. **测试环境修复（授权披露）**：PG 套件 17 个失败 = 漏设 `LANGGRAPH_STRICT_MSGPACK=true`（非缺陷）；`model_state.provider_turn` 删除 1 行 2026-09-06 Day 4 测试种子残留（合成常量，只读查证后精确 DELETE）。
5. **Task 3**（`636129c`）：探针重设计——`probe_model_capability.py` 5 判据（每项对应真实踩坑）+ 7 离线测试；付费执行 2 次合计 <$0.001（授权 ≤$0.01），**5/5 PASS，探针门从 FAIL 翻转为 PASS**。新坑实证：探针离线全绿、真实运行连爆 3 个装配缺陷（clock 协议/turn_store 构造/窄快照 vs 全文件）——坑 54 再验。

终态：离线 **846 passed, 129 skipped**；Ruff 全绿；PG **128 passed**。commit 链：`3a71357`→`b1c9a4e`→`1bdcf78`→`4ceab5c`→`636129c`→`de66c21`（未 push）。

**新会话任务**：Task 4 → 5 → 6 → 7→8→9 → 10（付费已预授权）→ 11，按计划文档顺序与判据执行；每 Task 红绿 + owning tests + 全量 suite + Ruff 后 commit（已打包授权）。

### 2.13 Task 4：v2 decide 三方一致性守卫（Claude Code，2026-09-14 续会话）

用户指示「读 HANDOFF 继续 Task 4、先起动 powercontext」后完成（commit **`84180e2`**，细节见执行日志 §6）：

1. **修复面**：`retail_graph.py` 图白名单 `_RETAIL_TOOL_NAMES`（v1 残留含 `execute_readonly_sql`）→ `RETAIL_TOOL_NAMES`（与 v2 `retail_decide` 规则四工具精确相等）；`bird_c_responder.py` 内联集合提升为 `BIRD_C_TOOL_NAMES`；守卫测试 2 → 6（三方精确相等：profile 规则 == 图/端口 allowlist == 冻结契约）；legacy resume 测试换 `retrieve_retail_knowledge` + 死代码清理。
2. **旧探针退役**：`scripts/probe_deepseek_gateway.py`（928 行）+ 其单测（16 项）git rm；未跟踪的 deepseek live 测试磁盘删除。依据：`636129c` 已宣布替代 + Gate P 正式退役 Day 3 固定 SQL 链判据 + fake 路径与新 allowlist 结构性冲突（全量 suite 首跑 3 failed 抓到）。
3. **披露**：`bird_c_responder.py` 为未跟踪文件（Day 2 时代、非 gitignore、从未入 allowlist；同状态含 context_builder/builder.py、model/* 等核心模块）——本次未卷入窄主题 commit，是否入库待用户裁定。
4. 终态：离线 **834 passed, 128 skipped**（846+4−16 / 129−1）；Ruff 全绿；PG **128 passed**。PowerContext 服务本会话开场起动（端口 8000）。
5. **Band 澄清（用户输入，零付费，两轮后定稿）**：「peak 计费」说法经用户提供官方峰值窗口定义后撤回——原对账（sim $0.6288 / 2.24× / 457.6 元 FAIL）恢复，band 模型（快照窗口 + `_pricing.py` weekday 门）经官方定义**精确确认**；新增付费运行调度纪律（peak = 2×，一律空闲档运行，Pilot 已满足）。账本、Pilot 报告 §9、HANDOFF §2.10、执行日志 §9 已同步。

### 2.14 Task 5：c/a 策略修复（Claude Code，2026-09-14 同会话续）

用户指示「直接继续 Task 5」后完成（commit **`ad961e8`**，细节见执行日志 §10）：

1. **prompt-policies v3**（新文件）：`bird-a-policy-v2` = 隔离 envelope 原样 + 官方策略（9 工具 coin 成本逐项==冻结契约断言、探索先行、先验证再提交、失败 debug 重试、P2 纪律）；`bird-c-policy-v2` = envelope 原样 + `max_turn` 上限声明；retail/common 零改动。**官方 a-mode 预算实为 `6+2×歧义+2×patience`（任务相关），policy 锚定 `budget_remaining` 不硬编码。**
2. **c-mode 澄清预算闸**：`BirdSessionStatePort` 按 state `max_turn`（orchestrator 种入，`cinteract.py:115-124` 一手核验）闸 ask_user——耗尽后提醒替换结果（原 call_id，坑 57），经 **answer 通道**回流（`_answer_text` 提取 `answer` 键；a-mode gate 的 `text` 键在该通道会丢——实现期发现）；每轮 phase datum 注入实时预算行；`_ask_user_turns` 按 phase 重置；max_turn 缺失 → gate 关闭。
3. **判据达成**：离线演练 c-mode 修复后行为（2 澄清 + 1 闸提醒 + submit，model_turns=4）✓；三轨不交叉全绿 ✓；离线 **844 passed, 128 skipped**（+10）；Ruff 全绿；PG **128 passed**。
4. **Task 11 输入**：c-mode 敏感度表入账本（N=10 每集 $0.0069 / c 侧 740 集 $5.11，较失控基线 **-83%**）。

### 2.15 Task 6：Runner SIGINT 恢复演练（Claude Code，2026-09-14 同会话续）

用户指示「继续下一步」后完成（commit **`f6af9f1`**，零产品代码改动，细节见执行日志 §11）：

1. **交付**：`tests/integration/evaluation/test_runner_sigint_recovery.py`（`-m postgres`，1.99s）——**Day 6 验收门② PASS**，与 §19.3 末条逐字对应。
2. **演练**：4 题清单（concurrency=1）；executor 在 hang 题 `stop_event.set()`（= `cli._bridge_signals` 的 SIGINT 等价物）后挂起 → 断言 stopped summary、hang 行 `interrupted`、**pending 题零 attempt 行**（`finish_attempt` 对未注册 id 抛 conflict 被 `_mark_abandoned` 捕获）；重启同 experiment → completed 不重跑、interrupted 题 **attempt_seq=2 从头跑**、pending 照常；事件 JSONL 10 条精确序列（`attempt_interrupted` 无 status 键——事件类型即信号）与 PG 行交叉；finally 按序清理归零。
3. **终态**：PG **129 passed**（+1）；离线 **844 passed, 129 skipped**（+1 skip）；Ruff 全绿。

### 2.16 Task 7：SSE 事件面（Claude Code，2026-09-14 同会话续）

用户指示「继续下一步Task7」后完成（commit **`3003ca5`**，细节见执行日志 §12）：

1. **migration 0006**（`0006_day6_trace_read_view`，已应用）：`ops_read.product_trace_events` 安全屏障视图（恰 8 列公开面）授权现有 `agent_reader`（= `PRODUCT_DATABASE_DSN` 身份）；基表 ACL 零改动。**新坑：alembic revision id ≤32 字符**（首版 33 字符撞 version_num varchar(32)，事务性 DDL 回滚零漂移后缩短重跑）。
2. **`src/commerce_agent/api/`**：稳定事件面 = 11 种 `TraceEventType`；载荷 = safe_summary 白名单 + run/attempt/sequence/reason_code，**排除 node/phase**（§20）；游标 = row_number 稳定序（trace sequence 是 attempt 内序号会撞号）；Last-Event-ID 重放 + 空闲心跳 + `create_app` 可注入源。fastapi 0.141.1 入依赖。
3. **判据达成（PG 3 条）**：完整闭环 13 事件经真库推送序列逐条一致（未审批/拒绝/修复/恢复全覆盖）、Last-Event-ID 断点续推、视图列白名单核验。
4. **新坑：starlette 1.6 TestClient 缓冲整个响应体**——无限 SSE 流经 `client.stream()` 必挂（portal 跑到完成为止）；流式行为一律直接驱动 async 生成器测试。
5. **终态**：离线 **855 passed, 132 skipped**；Ruff 全绿；PG **132 passed**。

### 2.17 Task 8 Step 1：Demo 布局（Claude Code，2026-09-14 同会话续）

用户「继续下一步Task8」启动 Step 1（`79de29b` 初版 → 用户定位反馈 → `7d3881e` 重做）→ **用户确认满意（计划门通过）**。细节见执行日志 §13：

1. **目标用户定位（新入 HANDOFF §1 语义）**：电商运营团队三角色——运营分析师（工作台主用户）/ 运营负责人（审批）/ 平台团队（评测）。初版偏工程师视角被用户纠正后按分析师答案视角重做，风格走 `minimalist-ui`（暖白单色 + 粉彩语义色 + 无 emoji）。
2. **web/ 工程**：Vite 7 + React 19 + TS 5.9 + ECharts 5.6，独立 lockfile，`web/.gitignore` 排除 node_modules/dist/*.tsbuildinfo；`npm run build`（tsc+vitc）干净。
3. **Step 2/3 必读约束**：①模拟载荷（SQL/表格/图表）demo-only，真实 SSE summary-only——需**只读工件 API** 决策；②`buildView`/`stageOf` 为真实事件复用种子；③评测中心真实数据需 **eval schema 读授权**（无角色可读——migration 0007 或等效，延续 0006 模式）；④dev server 已停（`cd web && npm run dev`）。
4. 终态：离线 855/132、PG 132、Ruff 全绿。本会话累计 12 commits（Task 4 → 8 Step 1），未 push。

### 2.18 Task 8 Step 2/3：SSE 客户端 + 只读 eval API + 评测中心真实数据（Claude Code，2026-09-14 新会话续）

用户「读 HANDOFF.md 继续 Task 8」后完成（细节见执行日志 §14，待 commit）：

1. **工件 API 决策（按打包授权行使推荐）**：live 模式 v1 = summary-only 事件面直驱（decision_summary → 结论/计划/对账、SQL 双指纹 + reason_code、evidence chips、refs）；富工件（SQL 文本/结果表/图表）不持久化、不新增写路径——推迟 Day 7（需 QueryEngine 写路径 + 新 ACL，超出 §22 UI 收窄）。
2. **migration 0007**（已应用，head）：`ops_read.eval_experiments`/`eval_attempts` 视图（attempt = attempt LEFT JOIN result + telemetry 白名单三列 agent_cost/simulator_cost/agent_turns）；**owner=evaluation_owner**（基表 ACL 零改动，比 0006 更严）；授权现有 `agent_reader`。
3. **API**：`api/eval.py`（/api/eval/experiments 聚合 + /attempts 明细）、`api/runs.py`（/api/runs 运行目录）、`create_postgres_app` env 装配 + `scripts/run_api.py`（win32 selector-loop 启动器，坑 59）+ `scripts/seed_ui_live_run.py`（一次性验证种子，已 reset 归零）；uvicorn 0.52.4 入依赖。
4. **web**：`api.ts`（按 11 事件类型 addEventListener 的 EventSource 客户端 + 原生 Last-Event-ID 重连）、`view.ts`（summary-only 投影 + 共享 stageOf，未映射事件回退显示事件类型）、`App.tsx` 演示/实时双模式、Workbench 指纹芯片/无 meta 提案/无答案澄清适配、EvalCenterPage 真实数据 + 断连回退演示快照并标注、vite `/api` 代理。
5. **坑 3 个新（§6.9 坑 59–61）**：uvicorn 0.52 win32 硬编码 Proactor（policy/factory 内设置均太晚）；vite 只绑 `::1` + 代理对上游断开传播延迟；React StrictMode 双挂载事件重复（cursor 去重修复，10/10 精确）。
6. **浏览器实机验证**：评测中心真实 Pilot d 数据逐格一致（18+1 failed+1 infra，rewards 全 0，$0.0000 成本卡诚实标注 spool 未回填）；实时模式 SSE 10 事件 → 投影正确 → 杀后端重启自动重连仍恰好 10 事件。种子场景 reset 归零（trace 0 行；eval 4 实验为 Pilot 原有）。
7. **终态**：离线 **866 passed, 137 skipped**；Ruff 全绿；PG **137 passed**；migration head=`0007_day6_eval_read_view`；`npm run build` 干净。**Task 8 三步全部完成；下一步 Task 9（tests/e2e 验收门①成文）→ Task 10（付费 ≤$0.10 空闲档）→ Task 11。**

### 2.19 Task 9：安全/E2E（Claude Code，2026-09-14 同会话续）

用户「继续下一步」后完成（细节见执行日志 §15，commit 入库同轮）：

1. **交付**：`tests/e2e/test_product_chain_api.py`（PG E2E 3 条）+ `tests/e2e/test_ui_acceptance.md`（手工验收清单，四节：演示模式/实时模式/评测中心/隐私红线 + 启动说明 + 两验收门对照）+ `tests/e2e/conftest.py`（win32 selector policy + postgres skip 门控，镜像 integration conftest 承重件——e2e 目录不继承 integration 的 conftest）。
2. **E2E 设计（纯读侧 + 真实装配）**：三闭环审计 fixtures（经营调查 13 事件含驳回→批准→执行读回→恢复；卖家风险/指标预警各 3 事件 proposal→approval→execution+audit_ref）经真实 trace store 写入三个 scoped 场景 → 消费只走 agent_reader：`create_app` 全三源装配，REST 经 TestClient、SSE 直驱生成器。判据：工作台链路 REST+SSE 全程序列==审计、三闭环各一次审批/执行/读回、评测中心 REST 读回、**载荷白名单（每条 SSE data 键集 ⊆ §18 十六键、无 node/phase）**。
3. **Playwright 决策（按授权行使推荐）**：不引入（§21 必选清单未含、IAB 实机验证已在 Task 8 完成）；清单留 Day 7 裁定口。
4. **过程修正（2 处测试自身）**：async fixture 内误用 asyncio.Runner（running loop 冲突）→ 改 async 直驱；审计 fixtures 同一时间戳导致 SSE 按 `(occurred_at, attempt_id, sequence)` 排序把 recovery 排最前 → 时间随事件递增。
5. **终态**：PG 全套（integration+e2e）**140 passed**；离线 **866 passed, 140 skipped**；Ruff 全绿。**Day 6 两项验收门齐备：①主产品链路可演示（手工清单 + Task 8 实机验证）、②Runner 中断恢复（Task 6）。下一步 Task 10（付费 ≤$0.10，空闲档）→ Task 11。**

### 2.20 Task 10：小样本策略验证执行（Claude Code，2026-09-14 深夜，付费已行使）

用户指示「按 §16 清单执行 Task 10」后完成（报告 `docs/reports/2026-09-14-task10-strategy-validation.md`；执行日志 §17；三次运行、两 infra 发现、submit 通道贯通）：

1. **准备**：空闲档（23:08–23:49 北京周一晚）✓；config-hash 派生方式复核确认 = **task-selection.json SHA-256**（Task 13 旧值精确复现），实验级 `97a40e74…ad749`；per-profile 参考指纹现场复算与 §16 精确一致。
2. **Run 1**（`…20260914`，a+c 并发 2）：c 集 2.4s failed（**同题双模式并发 createdb 竞态**——官方 task DB 命名不含 mode，两集同时 drop/create 同名库；Task 13 异题故未触发）；a 集 succeeded 116s 但第 8 轮模型调用被网关 fail-closed `provider_response_invalid` 中断（前 7 轮 reported：探索+知识+SQL+2 澄清、13/18 coin——行为形态远好于 Pilot）。a 保留原判；c 按 GC7 补跑。
3. **Run 2**（`…b`，c concurrency=1）：succeeded 但 **60 ask/0 submit/551s** = Pilot 失控复现 → **根因 = 镜像陈旧**（烘焙于 09-13，Task 5 闸 + v3 配置从未入 live；容器内 grep 实证 0 命中、仅 v1 配置）。**离线全绿 ≠ 镜像可用**（坑 54 再变体）。
4. **镜像重建 + Run 3**（`…c`）：容器内实证闸/v3 → c 集 **succeeded 118s：12 ask_user + 2 submit_sql，提交到达官方评审**（结构化 Phase 1 判定 failed，reward 0.0 合法）。**判据：submit_sql ≥1/集 ✓；reward>0 ✗（SQL 质量结果，交 Task 11）**。
5. **成本**：agent 实测 **$0.025047**（0.011539890+0+0.009389802+0.004117392，band 全程 off_peak）；sim 估 $0.05–0.08（~77 调用 × Pilot 均价）→ 合计估 $0.075–0.105（上限边界；**本 episode 不再付费**）；权威数字待用户余额核对。账本新增 `task10_strategy_validation` 节。
6. **遥测导入**：Run 3 经 Task 2 importer 首次真实入库（14/14 文件 fail-closed 通过；`BIRD_EXPERIMENT_ID` compose 默认值 → 确定性改挂并披露 = Day 7 接线缺口）；Run 1/2 旧格式不可回填（Pilot d 同款裁定）。
7. **交 Task 11 输入**：修复后 c 集 agent $0.0041 + sim ~$0.013 ≈ **$0.017/集**（优于 Task 5 敏感度口径）；a-mode 改善获轨迹支持、待完整 episode 实证。**下一步 = Task 11（零付费对比分析，交用户裁定）。**

### 2.21 Task 11：Full 重设计方案对比（Claude Code，2026-09-15 新会话，零付费收官）

用户指示「执行 Day 6 收官 Task 11，完成后停下等裁定」后完成（交付 `docs/project/research/2026-09-15-full-redesign-options.md`，commit **`6ef0c91`**；细节见执行日志 §18）：

1. **方向 0 重推（现状范围 + 修复后单价）**：乐观 147.9 / **基准 201.6 元 = 1.26× FAIL** / 保守 298.6 元 vs 160 线。策略修复本身砍掉 ~56% 总账（Pilot 终裁 457.6 → 201.6）。
2. **修正建模披露**：Pilot FAIL 的 sim 平摊（$0.03144/集）对 a-mode 高估 ~8–10×；Task 10 per-call 数据（~$0.001/调用）修正后 a-mode sim ~$0.003–0.004/集、a-mode Full 剩余 ~96 元——**「a-mode 单项 210 元超线」反转**，「Full 数学上不可行」软化为「1.26× 可用温和杠杆收敛」。Pilot FAIL 不回溯推翻（当时数据下保守裁定正确）。
3. **三方向**：A 范围压缩 = 唯一两情景过线杠杆（A1 c-only 105.7/152.6、A2 200/模式 88.5/129.0、A3 300/模式 116.8/171.4 保守微超、A4 = A3+B 91.6/135.6；偏离 §17.3 = 用户修规格）；B sim 降本退化为放大器（sim 总盘已从 69% 占比降到 ~86 元；deepseek-flash 或已是价格地板；**官方规程 sim 模型合规排查 = 开放项**）；C 上限修正 = 260 覆盖基准 / 320 覆盖保守（修 §16.4 + 总预算两处冻结规格）。
4. **reward 侧**：A/B/C 增益均 = 0；19/19 集 reward=0，任何 Full 变体现启即全 0 分；最便宜信息单价 = 能力验证 2–4 集（~$0.05–0.10，需新授权）。
5. **建议序列**：① 现在不启动；② 能力验证（reward>0 门）作前置；③ PASS 后首选 A4、全量可比性优先则 C；④ 有界失败 → A1 最小工件或搁置。强制 riders：off-peak-only / 每 25 集重估 / Task 10 三修复项入 Day 7 / 首个 a 集兼作成本验证（超 $0.035/集安全暂停）。**裁定清单五项在研究笔记 §9，等用户逐项裁定。**

### 2.22 能力验证执行（Claude Code，2026-09-15 晨，付费裁定行使；细节见执行日志 §19）

用户晨间裁定（①批准 ②A4 ④纳入 Day 7）后执行（窗口 07:47–08:50 北京周二，全程 off_peak，09:00 peak 前完成全部付费调用）：

1. **执行**：选题 seed 13（`archeology_scan_8` c + `archeology_scan_7` a，零任务重叠）；镜像容器内实证 ✓；experiment `…a`：c 集 15.5s `official_task_error`（瞬时 infra，GC7 废弃）、a 集 succeeded 181s（**v3 策略下首个完整 a-mode episode**：11 轮、7 SQL 实验、2 澄清、2 提交、预算 20 内）；experiment `…b`：c 重跑 succeeded（14 ask + 2 submit）。
2. **判据：reward>0 未达成**（4 次提交全败 Phase 1）。成本：agent 实测 **$0.034841** + sim 估 ~$0.022 ≈ **$0.057**（上限内，账本 `task11_capability_validation` 节）。
3. **决定性机制发现（本轮最大增量）**：**官方 c 模式每轮新建 agent 会话**（spool 实证 16 会话 × 恰 1 轮；a 模式单会话连续）→ 每轮上下文仅 phase record → record 不携带任务问题 → 第 0 轮真实澄清后逐轮失忆（agent 自述无问题/schema）→ 官方 sim out-of-scope 拒答 → 占位符 `SELECT 1` 提交。**回溯：Task 10 Run 2/Run 3 c 集均含同款失忆句式——Task 5 闸治症状未治机制；c 模式全部历史 reward=0 由此解释。** 待静态核验定性：我方 adapter 会话状态复现缺陷 vs 官方本义（若官方本义如此则需跨轮记忆策略）。
4. **对方案的影响**：预算面不变（c 集 $0.017 锚点成立）；能力面 c-mode 结构性 0 直至修复闭环（Day 7 第一项：静态核验 → 修复 → rebuild + 容器实证 → 1–2 集再验证，新付费授权）；a-mode Phase 1 失败 = 真实 SQL 质量。**A4 Full 启动继续等修复闭环 + 用户裁定。**
5. **次日坑新增（§6.11）**：编排器子进程 stderr 无落盘（official_task_error 不可追溯）；官方库冷启动窗口内 sim 连不上 5433（schema 加载失败 ×2，观察项非主因）。

### 2.23 余额核对闭环 + Day 7 计划产出（Claude Code，2026-09-15 晨续，零付费）

用户报数（09-14 平台 0.65 元 / 09-15 晨 0.32 元）并批准 Day 7 计划 Gate 后完成（commit `2b6c6c26`）：

1. **对账拆分**：Task 10 sim = 0.65 − agent 0.177 − 探针 0.007 = **0.466 元 = $0.0659**（估算带 $0.05–0.08 内 ✓，每调用 ~$0.00086——失控长会话为主）；验证日 sim = 0.32 − 0.246 = **0.074 元 = $0.0104**（每调用 **~$0.0005**——修复后短会话）。累计已花 = 6.43+0.65+0.32 = **7.40 元**。账本新增 `simulator_side_authoritative` ×2 + `sim_anchor_refinement_2026_09_15`。
2. **锚点精化**：sim 每调用与上下文长度相关 → c 集 sim $0.013→**$0.008**、c 集合计 $0.017→**$0.012**（a 集 $0.023 不变）；**A4 重推更新：基准 ~82 元 / 保守 ~125 元 vs 160 线**（原 91.6/135.6）。研究笔记 §11 已回填。
3. **Day 7 计划产出（待批准）**：`docs/superpowers/plans/2026-09-15-day7-cmode-fix-a4-and-closure.md`——5 Phase 10 Task：A 静态核验→修复（红绿）→rebuild+实证（零付费）；B 再验证（**Gate C1 ≤$0.05**，判据 reward>0）；C sim 合规排查 + a-mode 诊断 + A4 清单/重估/四修复项（零付费）；D A4 分批执行 + 消融 + 产品 50 题（**Gate C2**，启动前重估表呈用户）；E 收尾报告/README/面试材料。预算工作数字：基准 ~116 元 vs 160 线（余 28%）。

### 2.24 Day 7 Phase A：判决书 + c-mode 修复闭环（Claude Code，2026-09-15 晨，零付费，用户批准计划后）

用户「批准执行」Day 7 计划后，Phase A（Task 1–3）当轮完成（fix commit `4287a0b`）：

1. **Task 1 判决书**（`docs/project/research/2026-09-15-cmode-phase-record-verdict.md`）：**缺陷 = 我方 adapter**，官方语义无缺陷。官方一次 run_session 承载整个 clarify 循环：问题在首条消息（`User Query:\n{amb_user_query}`）、`db_schema`/`external_kg` 在官方种子 state（c 模式无 schema 工具，state 是唯一通道）、对话靠 ADK 会话记忆累积。我方 `_run_c` 每轮仅以最后 sim 回答重建上下文（三者全丢）。**更正**：16 spool 文件 = 我方每模型轮新 attempt_id（非官方多会话），机制结论不变。
2. **Task 2 红绿**：RED = 修复前第二轮上下文实测 `'2018\n\n[clarification budget: 1 of 5 …]'`；GREEN = `_phase_content(task_message, dialogue, state)`（User Query + Task schema + External knowledge + 对话累积 + 预算行）+ `_run_c` 保留消息累积 dialogue。适配器 21 条全绿。
3. **Task 3**：Ruff 全绿；离线 **867 passed, 140 skipped**；镜像 rebuild + 容器内实证 ✓。
4. **C1 排程**：距 peak 不足，按纪律排下个空闲档（一键清单见 §3）；风险披露：schema 渲染抬高每轮 token，c 集锚或上浮（C1 实测回填）。

### 2.25 Day 7 Task 4 / Gate C1 执行：reward=0，残余缺陷定位（Claude Code，2026-09-15 午，付费 Gate 行使）

用户「按 HANDOFF §3 清单执行」后，12:34–12:44（空闲档）完成（细节见执行日志 §21）：

1. **前置**：`4287a0b` 后代码零变更 → 跳过 rebuild；config-hash 现场复算精确一致；官方库就绪探测 + 容器内 `_phase_content`×2 实证 ✓。
2. **运行**：`task7-cmode-refit-20260915` / attempt `a5073903…`，c 单集 archeology_scan_8，**succeeded 118.4s**，5 模型轮（3 ask + 2 submit），reward **0.0**（两次 Phase 1 失败）→ **能力门未过**。
3. **判据拆分**：真实 SQL ✓（零 `SELECT 1`，CTE+真实列，澄清引用 knowledge #37/#51/#17）；零失忆 ✗——**失败反馈轮复现失忆句式**（agent 如实自述无原始问题）。
4. **残余缺陷（有界诊断 ≤1 轮）**：修复覆盖 clarify 循环、未覆盖 **Phase 边界**——submit 失败 → 官方新 Phase → `_run_c` 局部 `dialogue` 清空（:434）+ feedback 被当 User Query 渲染（:84），任务问题仅存于首 Phase message、无持久化；state 级 `dialogue_history` 在场未用（:270-273）。修复方向：首条消息入 state + dialogue 自 state 播种（红绿→rebuild→C1 重验）。

### 2.26 Day 7 残余修复执行：Phase 边界会话记忆（Claude Code，2026-09-15 午续，零付费，未 commit）

用户裁定「执行① 批准残余修复」后当轮完成（细节见执行日志 §22）：

1. **官方语义定案**（`cinteract.py` 141/159/179 一手核验）：整任务只 init 一次 ADK 会话，Phase 1 / debug / follow-up 全部同一会话、记忆跨 Phase 累积——debug 轮 agent 本可自带原问题、对话与自己提交的 SQL。
2. **红绿**：2 条 RED（失败反馈轮 + follow-up 轮，坑 70 边界枚举）首跑 FAIL 且失败输出精确复现 C1 实况 → GREEN：`_Session` 持久 `_task_message` + `_memory`（ask 对 + submit SQL + 提交结果截断），`_phase_content` 新增 `current_message` 渲染槽。适配器 23/23。
3. **终态**：离线 **869 passed, 140 skipped**；Ruff 全绿；rebuild + 容器内实证（新符号 3 组全命中）✓。
4. **C1 重验 staged**：新 experiment `task7-cmode-refit-20260915b` + `events-c1-refit-b.jsonl` + 同单集/config-hash、≤$0.05、off-peak——**等用户授权后按 §3 清单执行**。

### 2.27 C1 重验执行：provider_response_invalid 两连败，INCONCLUSIVE（Claude Code，2026-09-15 午后，付费 Gate 行使；细节见执行日志 §23）

用户「两者一起授权」后：入库 `0f99076`（fix）+ `bd38072`（docs）→ 13:22 重验 b（97s failed，2 轮 $0.004848）→ GC7 补跑 c（65s failed，1 轮 $0.003392）→ 两连败同因 **`provider_response_invalid`**（网关解析 DeepSeek 响应失败，503 → 编排器 official_task_error）→ 按纪律停止。

1. **修复未被证伪**：失败在 provider 响应解析（`gateway.py` `_parse_output`/`_finish_reason`），episode 未活到 debug 轮——phase-memory 修复 live 未验证。Task 10 Run 1（旧镜像）同 reason 先例 → 非本次修复引入。疑似：长 reasoning 下 tool_call arguments 截断。
2. **精确触发未知**：失败响应不留存（Day 3 fail-closed 设计）；需插桩诊断（sanitized 元数据落盘）才能定性。
3. **成本**：重验授权内 $0.00824 ≤ $0.05 ✓（首验 $0.014710 已入 §2.25 节）。
4. **裁定选项**：①插桩诊断 + 1 次付费运行 ~$0.01；②纯重试；③转 Task 5/6 零付费、验证排晚窗。

### 2.28 方案1 执行：插桩定性 + 输出预算修复（Claude Code，2026-09-15 午后，诊断付费 + 修复零付费，未 commit）

用户裁定「执行方案1」后完成（细节见执行日志 §24）：

1. **插桩红绿**：gateway 两个 fail-closed 解析点落 `protocol_diagnostic` sanitized 日志（零响应内容）；2 RED → GREEN。
2. **诊断运行 d 一发命中**：`site=output_parse error=ValidationError`（FinalOutput 空串）+ `finish_reason='length'` + 回显匹配。**根因 = reasoning 吃满 8192 输出预算 → finish=length + content 空 → 输出校验炸**；非协议损坏、非重试语义问题。Task 10 Run 1 同解释。$0.008127 ≤ ~$0.01 ✓。
3. **修复红绿（零付费）**：`ThinkingConfig` le 8192→16384 + run-profiles bird_a/bird_c 推理 16384（profile revision v2，canonical 重写，retail 不动）。**不抬正常轮成本**（成功轮本就 <8192）。终态 873 passed + Ruff 绿 + rebuild 实证 ✓。
4. **C1 重验 staged**：experiment `…e` + `events-c1-refit-e.jsonl`，~$0.015 全程锚，**排晚窗（18:00 后），等授权**。残余风险：16384 下 reasoning 仍可能耗尽（概率大降，插桩可立即取证）。

### 2.29 C1 重验 run e：16384 仍不足，水位线=官方思考默认 64K（Claude Code，2026-09-15 晚窗，付费 $0.011330，停于纪律）

用户「授权执行下一步」后 18:04 晚窗执行（细节见执行日志 §25）：

1. **运行**：`…e` / attempt `7e6c1ef7`，failed 196s——4 正常轮后第 5 轮 `finish_reason='length'` + 空 content，同 `FinalOutput` 校验炸。插桩当场定性。
2. **H1 排除 / H2 成立**：官方文档（更名探针双源核验）`max_tokens` 上限 384K → provider 如实接受 16384；该轮 reasoning 真实吃满 16384。**官方思考默认输出 64K（max effort 128K）**——官方 ADK 不覆写 max_tokens，天然 4× 余量。
3. **结论**：低于 64K 的上限都让偶发 reasoning blowout 保持致命；两轮修复（phase-memory + 16384）未被证伪，死因始终在输出预算。
4. **裁定选项**：① 65536（纯配置=官方等效，blowout 轮最坏 ~$0.04 off-peak）② 空轮优雅处理（触 Day 3 fail-closed 契约）③ ①+②；可选：读 1 个官方 agent 配置文件确认官方不设 max_tokens（allowlist 扩展待确认）。

### 2.30 64K 水位线 run f +「纯文本轮」缺陷修复（Claude Code，2026-09-15 晚，付费 $0.018509 + 修复零付费；细节见执行日志 §26）

用户「按推荐决策执行下一步，并给予授权」后：

1. **入库**：`4a1946e`（插桩）+ `ce3bdaf`（64K 水位线：ThinkingConfig le=65536 + bird_a/c profile v3）+ `38ec763`（docs）。
2. **run f**（`…f` / attempt `d023d724`，18:21–18:24 晚窗）：failed 158s，$0.018509。**诊断日志零命中 = 输出预算根因已消除**；clarify 阶段**首次完整走通**（submit 达官方评审），debug 轮 reasoning 18.3K / completion 18.9K（16384 下必死，64K 下正常）。
3. **新缺陷**：debug 轮模型纯文本作答（无工具调用）→ `bird_c_candidate_required` 400 杀整集。官方语义：非工具调用响应 = 结束 runner 调用，非错误。
4. **修复（`26047e2`，零付费红绿）**：`TextCandidate` 入 union + responder 文本分支 + `_run_c` 文本轮记 `[agent note]` 入记忆并结束本轮 run_session。**874 passed**；Ruff 绿；rebuild + 容器实证 ✓。
5. **run g staged**：`task7-cmode-refit-20260915g` + `events-c1-refit-g.jsonl`，~$0.02–0.05——**等授权**（坑 5）。

### 2.31 run g：首个端到端健康 episode（Claude Code，2026-09-15 晚窗，付费 $0.018851；细节见执行日志 §27）

用户「授权执行下一步」后 18:39 晚窗执行：

1. **运行**：`…g` / attempt `accf1991`，**succeeded 161.9s**——2 ask + 2 submit，agent **$0.018851**（off_peak），边界/诊断日志零命中。
2. **判据① PASS**：零失忆句式、两次提交均为真实 SQL（mesh CTE + PRU CASE）、debug 轮真实修订重提、第 4 轮 reasoning 15.2K 在 64K 下正常完成（8192/16384 下必死）。
3. **判据② FAIL**：reward=0,两次 *"Test case execution failed"*（SQL 运行期错误,非结果不匹配）→ **能力门未 PASS；剩余差距 = 真实 SQL 质量**（与 a-mode 发现同类）。GT 隔离内无法更深定位（任务 schema 在官方 state、未持久化到 agent 可读工件）。
4. **锚点**：c 集 ~$0.019/集 agent + ~$0.002 sim ≈ **$0.021/集**；A4 影响 ~+$1.5 可忽略。
5. **裁定选项（§3）**：再验一集（异题更干净）/ 转 Task 5/6 零付费 / 接受现状进 C2 重估表（能力门状态如实标注）。

### 2.32 run h：异库交叉验证（Claude Code，2026-09-15 晚窗，付费 $0.009012；细节见执行日志 §28）

用户「按推荐决策执行下一步，并给予授权」（§3 选项 ① 异题再验）后：

1. **选题**：cold_chain_pharma_compliance_3 c（Pilot seed-7 选取、ambiguity 2、异库；复用 Pilot GT 拆分零新 GT 处理；config-hash `b1889777…9017` = Pilot 选取 sha256 现场复算一致）；新单题清单 `outputs/bird-pilot/pilot-task-list-c-refit.jsonl`（公开，待 commit）。
2. **运行**：`…h` / attempt `6332977d`，**succeeded 74.1s**，agent **$0.009012**——sim 真实回应第一问（"on-time delivery performance"），真实 SQL，debug 修订重提，零失忆/零 infra。
3. **判据**：①再次 PASS；②reward=0（同 *"Test case execution failed"*）。**跨库结论：结构健康稳定成立；reward=0 根因收敛 = c 模式对 JSONB 内键盲（无执行工具，官方预期 ask_user 消解，sim 能力有限）+ SQL 质量**。
4. **两集合计 $0.027863 ≤$0.05 ✓**；c 集健康锚 $0.009–0.019/集（题相关）。

### 2.33 Day 7 Task 5+6 零付费收官（Claude Code，2026-09-15 晚；细节见执行日志 §29）

用户「按推荐决策执行下一步」（②+③ 组合）后：

1. **四修复项**（`89814c5`，红绿 11 新测试，885 passed）：`compose_env_out`（坑 65）/ 同题双模式并发拒绝（坑 63）/ 子进程流落盘 + timeout kill（坑 68+58）/ `scripts/preflight_bird_stack.py` 官方库就绪探测 + 25 库基线（坑 67）。
2. **A4 清单**（`bc8db94`）：600 集 = 300c+300a、22 库全覆盖、seed 20260915；每模式批量清单；GT 拆分 gitignored。
3. **Task 5**：sim c/a 同配置成立（单一服务拓扑 by construction）、模型可换性确认（开放项：官方规程允许性）；a-mode 失败主因 = 知识查询 miss → 自造公式 + sim 拒答（与 c-mode 同源）。
4. **C2 重估表**：基准 ~135/160 ✓（余 16%）；保守 ~186 超线 16%；riders 四条呈批。
5. **成本/锚点**：agent 实测 **$0.014710**（spool 878→883，off_peak，prompt ~13k/轮）；sim 估 ~$0.0015；合计 ~$0.016 ≤$0.05 ✓。**c 集新锚 $0.016/集**（agent $0.0147 = 3.6× 旧锚，schema 渲染实证；sim 调用 60→3）；A4 影响 +~$1.2 可忽略。账本 `task7_c1_refit_20260915` 节。
6. **现场**：栈与官方库已 stop；eval 库新增实验（1 attempt succeeded，证据保留）；**下一步付费运行需用户新授权**。

### 2.34 A4 a 批执行与截停：223/300 + v4 验证 + 并发 2→4（Claude Code，2026-09-16，付费行使 + 两次用户裁定；细节见 `docs/reports/2026-09-16-a4-a-batch-truncated-and-v4-validation.md` 与执行日志 §32）

用户裁定链：①「a 批照跑」→「授权 v4 验证」→「A」并发升档 →（GC7 自动停）→「②」截停。沿 c 批模式（12×25 子批、每批 preflight + compose env 重建 agent 容器 + 后台 runner + 聚合入账本）：

1. **b01（午窗）**：25/25 succeeded，$0.0274/集，首个 a 集成本验证 PASS（<$0.035）；b02 会越 14:00 peak 线 → 只跑 b01，停栈（晚窗富余 15h，零排期压力）。
2. **v4 验证（cap $0.20 实花 $0.056）**：prompt-policies.v4 零付费实现（RED→GREEN，888 passed，rebuild 容器实证）→ 3 集验证：核心 scenario 零触发（知识全命中）、广义行为 2/3 激活且 sim 真实作答、reward 0/3 → **回退 v2**（reward 中性 + 300 集单一策略版优先），v4 归档 `outputs/bird-eval/prompt-policies.v4-20260916.json`。
3. **b02–b07（并发 2）**：零 infra；b04 首现 `ContextBudgetExceeded`（fake_account_24）。b08 起用户裁定并发 2→4：`RunnerConfig` 冻结 cap le=2（v0.3 §16.2）→ 一行改 le=4 + 钉测试 + 887 passed（宿主侧零镜像影响，规格偏差已披露）→ b08 试点 25/25 零 infra **~18 分钟/批（2.3×）**。
4. **b09 同因两现 → GC7 停**：sports_events_8 同死 ContextBudgetExceeded（对话依赖的任务级确定性失败：第 ~10 轮强制上下文超 64K prompt 预算；同库他集均成功；同配置重试必复发；修复需动 runs d/e 的 64K 输出冻结决策，不划算）→ 用户裁定②截停。
5. **终账**：**223/300 succeeded + 2 failed + 75 unrun（b10–b12 清单已派生未用）；reward 0/225；agent $6.1506 = 43.5 元（$0.0273/attempt，off_peak 100%）**；telemetry 导入 225/225 零歧义；spent ≈75.9 元，**总投影 ≈116/160 ✓**；栈与官方库已 stop。
6. **新坑**：见 §6.13（77/78）。

### 2.35 消融 §17.2 COMPLETE：120/120，修复价值 = 0（Claude Code，2026-09-17，付费门行使；细节见 `docs/reports/2026-09-17-ablation-repair-120.md` 与执行日志 §33）

用户「授权消融」（cap 25 元）后分两窗执行（A 条件估时贴 14:00 peak 线 → 按 off-peak rider 分窗）：

1. **条件 A 实现（零付费，`e0b3844`/`d70b429`/`e87391b`）**：官方 ADK 无修复旋钮 → adapter 实现「首次失败停止」——c-mode debug 轮零模型调用返回终止文本；a-mode `_ConditionalStopGate` 在失败提交后下一模型轮前拦截；默认关闭、`BIRD_ABLATION_CONDITION=a` opt-in；4 新测试 891 passed + rebuild 实证。
2. **选取**：seed 20260916，155 个 c/a 配对任务分层抽 30 题（30 库族各 1，GT 拆分已在盘零新处理）；config-hash = pool 文件 sha256。
3. **结果**：B（修复）60/60 + A（停止）60/60 = **120/120 succeeded 零 infra**；**P1 两条件 0/60 vs 0/60——修复救回 0 集**（§17.2 描述性结论：当前能力下修复价值 = 0）；reward 0/120 如实标注；B 多烧 2.4–2.7× 轮次/费用零 P1 增益；A 格恰 1.00 submit/集 = 停止语义活体确认。
4. **账目**：agent **$1.673449 ≈ 11.8 元**（cap 25 元内）；telemetry 120/120 精确归属（坑 79 修正后 DB 与 spool 逐分一致）；栈已 stop。
5. **执行修正**：混合清单触坑 63 → 拆单模式串行（坑 80）；B/a shell `&` 发射按坑 58 双时点核验。

## 3. 当前卡在哪里

**无阻塞——a 批截停收官（223/300）+ 消融 §17.2 COMPLETE（120/120），收尾段剩余 = 产品 50 题 + 最终报告。**

- **a 批终账**：agent $6.1506 = 43.5 元（225 attempts / 2,301 turns，off_peak 100%）；reward 0/225 如实标注；telemetry 225/225 精确归属。
- **消融终账（新）**：120/120 succeeded，agent $1.6734 = 11.8 元（cap 25 元内）；**修复救回 0 集**（P1 A 0/60 vs B 0/60）——§17.2 描述性结论 = 当前能力下修复价值 0，修复经济学为负（2.4–2.7× 费用零增益）。
- **增量重估**：spent ≈87.7 元（75.9 + 11.8）；forward（产品 ~23）≈ 23 元；**总投影 ≈111/160 ✓（余 ~31%）**。
- **能力门终态**：A4 + 消融全程 reward>0 = 0/645；机制 = 知识缺口 + SQL 运行期质量；v4 杠杆已验证「行为激活、reward 中性」归档备用；修复杠杆经消融实证同样无效。
- **上下文溢出**：bird_a 长对话 0.9% 确定性失败（2 集），裁定不修、如实标注。
- **待用户**：产品 50 题付费门呈批（§5）；sim 侧余额核对。

## 4. 当前验证证据（2026-09-11 Task 18 测试数字 + 2026-09-12 Gate G/清理现场，最终源码状态，Claude Code）

| 检查 | 精确命令 | 结果 |
|---|---|---|
| Ruff | `uv run ruff check src tests scripts db/migrations` | All checks passed |
| 默认离线 suite | `uv run pytest -q --tb=line` | 602 passed, 122 skipped, 1 warning, 12.36s |
| PostgreSQL integration（终态） | 开关 + `uv run --env-file .env pytest tests/integration -m postgres -q --tb=line` | **121 passed, 1 deselected, 25.58s**（首跑 15 failed 的完整闭环见报告 §4.3） |
| Owning tests（修复验证） | 同上，目标 `tests/integration/checkpoint` | 32 passed, 6.59s |
| BIRD isolation | `uv run pytest tests/unit/orchestration/test_track_isolation.py tests/unit/orchestration/test_bird_a_graph.py tests/unit/orchestration/test_bird_c_responder.py tests/unit/orchestration/test_bird_tools.py -q --tb=line` | 33 passed, 1.57s |
| Allowlist 安全扫描 | 仓库外脚本，119 文件 ×2 遍 | 130 命中逐类裁决 → 0 真实发现 |
| DB 现场 | 只读 psql（容器内 `commerce_admin`） | 11 表 0 行、0 sessions、开关 absent |
| Gate G commit（09-12） | 计划 G1/G2/G3：status 审阅 → add → `--cached --check` → commit | root-commit `d87768a`，108 文件 25,111 行，allowlist 精确一致，未 push |
| checkpoint 清理（09-12） | `psql -1` 单事务 DELETE ×3 + 前后行数核验（只读清点比对） | 42/8/257 → 0/0/0；11 张 Day 4 表 0 行、0 会话；`checkpoint_migrations` 10 行保留 |
| checkpoint 补录 commit（09-12） | 哈希复验 2/2 → add 恰 2 文件 → `--cached --check` → commit | `2c206ac`，2 文件 901 行，未 push |
| Day 5 Phase A（09-12） | 12 个实现 commit（`bc5db94`→`3079b60`），每步全量 suite 复核 + 逐 commit 授权 | **806 passed, 128 skipped, 1 warning**；Ruff 全绿；零付费零 GT 读取零官方 episode |
| 20 题选取（09-13） | `uv run python scripts/prepare_bird_pilot.py --dataset data/raw/bird-interact-full/public/bird_interact_data.jsonl` | 20 题（c10/a10，seed 7，10 库）；公开清单零 GT token；拆分 20 文件仅落 gitignored 目录 |
| 官方 db-check（09-13） | 同脚本 `--run-db-check`（checker 在 `_upstream/BIRD-Interact/env/`，5433） | `returncode 0`；22/244/2,011/273,571 与 Day 1E 基线 `all_match: true` |
| GT 拒绝检查（09-13） | `docker compose --env-file .env -f compose.bird.yaml run --rm --no-deps bird-system-agent python -c <零字节读探测>` | GT 路径与 db-env 挂载点均 `FileNotFoundError`；env 零 GT/凭据名；exit 0 |
| 修复后终态（09-13 preflight） | `uv run pytest -q --tb=line` + `uv run ruff check src tests scripts db/migrations` | **818 passed, 128 skipped, 1 warning**；Ruff 全绿 |
| Task 13 终态 suite（09-13 晚） | `uv run pytest -q --tb=line` + Ruff（含 bird_system_agent） | **829 passed, 128 skipped, 1 warning**；All checks passed |
| **Task 13 Pilot d（09-13 晚）** | `uv run --env-file .env python -m commerce_agent.evaluation --experiment pilot-day5-20260913d --purpose pilot --config-hash b1889777…9017 --task-list outputs/bird-pilot/task-list.jsonl --events outputs/bird-eval/events-pilot-day5-20260913d.jsonl --executor official --store postgres --adk-root _upstream/BIRD-Interact/BIRD-Interact-ADK --task-data-dir outputs/bird-pilot/task-data --episode-output-dir outputs/bird-eval/episodes` | **20 attempted：18 succeeded + 1 failed（crypto_exchange_9，episode 中段 503）+ 1 unfinished（cybermarket_pattern_12，official_process_failed）**；rewards 全 0（合法评测结果）；exit 0 |
| Pilot d 账本（§16.4） | spool 窗口聚合（600 文件/675 轮）+ PG attempts/results | prompt 1,747,080 / completion 253,547（reasoning 184,852）/ total 2,000,627 tokens；**agent 侧 $0.211203192 ≈ 1.4932 元**；写入 `outputs/bird-budget/pilot-ledger.json`；simulator 侧待用户余额核对 |

历史数字保留日期边界：Gate D `17 passed in 3.07s`（09-07）；Gate W `9 passed in 5.59s` + `1 passed in 2.77s`（09-07，本轮 121 内复确认）。更早的 `595 passed` 与 `70 passed` 为旧源码状态数字，不得作为当前证据。

命令级细节、负向授权矩阵（8 场景 × 0 写入）、状态转移、ACL 矩阵、SellerRef 敏感度比较、backtest historical-only、SqlReasoner/reconciliation 证据：全部在报告 §5-§13，逐条可回指。

## 5. 下一步计划

1. **本轮（消融）commit 授权**（用户）→ 报告 + 执行日志 §33 + HANDOFF/CLAUDE 刷新（§8.12）。
2. **产品 50 题**（Day 7 Phase D Task 8 / §17.1，付费 ~23 元）：产品轨 A/B 40 题 × 2 条件 + 封闭 10 题；执行前细估呈批。
3. **Task 9 最终报告 + README/面试材料**（Day 7 Phase E）：A4 口径 = c 300/300 全量 + a 223/300 + 2 确定性失败 + 75 裁定未跑；消融 §17.2 描述性结论（修复价值 0）；能力门如实标注；v4/并发升档/截停裁定作为过程决策入材料。
4. **可选**：4 份早期计划文件（Day 2a/2b/3/5）从未入库——补录与否待用户裁定；`cybermarket_pattern_12 [a]` 恢复裁定。

## 6. 踩过的坑，绝对不要再踩

### 6.1 授权、秘密与外部状态

1. **每个 Gate 独立授权。** Gate D/W PASS 不批准 Task 18、Gate P 或 Gate G；新会话不继承旧会话数据库授权。
2. **默认 skip 不是数据库证据。** 只有显式设置 PostgreSQL 开关并运行目标测试才算真实数据库验证。
3. **根 `.env` 不可人工查看。** 只允许 reviewed scripts 或 `--env-file .env` 消费；stdout 只保留 sanitized 状态。
4. **诊断只输出最小异常元数据。** 允许异常类型、reason_code、SQLSTATE、sanitized primary message；禁止 `pytest -vv --tb=long`，禁止输出 query params、payload、DSN、密码、HMAC key、grant、nonce、signature、private seller ID 或 server detail。
5. **一次正向运行就是一次授权额度。** 若失败后需要诊断重跑，先重新取得授权，不要把"诊断"当作免费重试。
6. **所有真实场景必须 finally reset。** 无论成功失败都核验 11 张表、application sessions 和两个测试开关。

### 6.2 Migration、PL/pgSQL 与 ACL

7. **migration 失败立即停止。** 不自动重试、不手工补 grant、不静默修 catalog。
8. **已应用 migration 源码改动会造成 catalog 漂移。** 只有在明确授权、精确 revision 核验和 11 张表为空后，才可受控 downgrade/reapply。
9. **`RETURNS TABLE` 输出名也是 PL/pgSQL 变量。** 所有同名表列必须使用 table alias 限定，尤其是 `execution_id`、`proposal_id`、`proposal_version`。
10. **最小 SELECT 权限不含行锁。** `audit_owner` 只有 SELECT 时不得使用 `FOR UPDATE`；不要用扩大权限掩盖函数设计错误。
11. **保留字必须处处一致引用。** `metric_alert_rule.window` 曾在 CHECK、view、INSERT 中因漏引号导致真实 migration 失败。
12. **JSON 运算符要显式括号。** 字符串拼接与 `->>` 混用时必须写成 `text || (json->>'field')`。
13. **表列权限不等于对象可访问。** 角色还需要相应 schema USAGE；executor receipt readback 曾因此失败。
14. **DELETE predicate 需要 SELECT。** reset owner 的最小权限是 `SELECT(scenario_id)` 加 DELETE，不要扩大为整表 SELECT。
15. **deny-path ACL test 由 admin 解析对象。** 受限角色可能没有 schema USAGE，不要让它用文本对象名做 privilege introspection。
16. **`SECURITY DEFINER` 必须同时满足固定安全 search_path、schema-qualified SQL、PUBLIC revoke、精确 owner 和最小 grant。**
17. **login role 与 function owner 不可混淆。** `product_scenario_reset` 是登录角色，`scenario_reset_owner` 是 NOLOGIN owner；owner 底层权限不能代替 login role 的 schema USAGE/function EXECUTE。

### 6.3 Adapter、并发与数据契约

18. **workflow pre-read 不能消除并发竞态。** deterministic identity 的后到请求若收到 `23505`，必须在事务回滚后读取同 proposal 的成功 receipt；只有读到 receipt 才能恢复，其他 identity conflict 保持 fail-closed。
19. **commit response loss 是 outcome unknown。** 先 readback，再决定是否重试；禁止盲目重复业务写入。
20. **psycopg 的 tuple 是复合值，不是数组。** PostgreSQL `text[]` 参数要绑定 Python list；不要把领域 tuple 直接传入。
21. **公共 Decimal 契约要在边界固定尺度。** SQL 除法可能产生高精度 Decimal；公开 `normalized_value` 必须与 backtest 一致量化到 6 位。
22. **跨实例恢复走 PostgreSQL 窄化 read path。** 不得退回实例内 dict/cache。
23. **private identity 只在受控边界内存在。** `ops_read.risk_annotations`、Trace、audit、checkpoint、report 和 public payload 只能出现 opaque SellerRef，不能出现 raw seller ID 或 keyed digest。
24. **Product Trace、execution audit、BIRD trace 不能混用。** 它们的目的、权限和证据含义不同。

### 6.4 编排、隔离与验证

25. **RetailGraph 只能持有 proposal-only port。** 不得把完整 `OperationWorkflow` 或 approval/execution capability 注入 graph。
26. **terminal state 先持久化，再清理 provider-private turns。** cleanup retry 只能 finalize，不能重复前序副作用。
27. **failure injection 比较精确 idempotency key。** 不比较 `repr`。
28. **`query:<step_id>` evidence ID 两端必须兼容。** 不要单边改生成或解析约定。
29. **SQL repair 三次预算属于整个 attempt。** 不是每个 plan step 各三次。
30. **BIRD 隔离是硬门。** Product imports、tools、profiles、constructors、fixtures、state、credentials 和 data 都不能进入 BIRD。
31. **只扫描显式 allowlist。** 仓库级递归扫描可能触及 evaluator-only 或 secret 内容。
32. **测试数字必须带范围和时间边界。** 历史 `595 passed` 不代表最终源码；新报告必须产生新证据。
33. **失败后执行 owning test，再执行捕获缺陷的 broader gate。** 不要只跑新单测就宣称修复完成。
34. **仓库没有 Git 基线。** unborn `main`、0 commits、项目文件未跟踪；空 `git diff` 没有意义。没有 Gate G 授权时保持文件原状，不执行任何 Git mutation。
35. **不要启动 subagent。** 除非用户在新会话明确要求 delegation/parallel agents。

### 6.5 Task 18 本轮新增（2026-09-11，Claude Code）

36. **测试断言不要硬编码 revision/契约值。** 升版时必须同步更新所有硬编码期望——15 个 Day 3 集成测试因硬编码 v1/把 v2 当非法值而在 Task 18 首跑翻转失败。修订变化点：`_checkpoint.py` 的两个 revision 常量 + 所有断言中的 revision 字面量。
37. **Day 4 之后六段式 legacy 流程的终局语义是 fail-closed。** raw FinalOutput 终局 = `stopped` + `typed_retail_terminal_required`（`retail_graph.py` `_outcome`）；不要把它的 `completed` 当作回归目标。这些测试真正守护的是 resume 幂等、预算保留、cleanup 重试与 checkpoint 隐私。
38. **报告数字只能来自已运行的命令。** 不要预写预测值——本报告初稿曾预写扫描 TOTAL HITS，终扫数字不同，已用实际值替换并机械复核。
39. **PowerContext handoff 提交流程（无 MCP 挂载时经 HTTP 桥接 `http://127.0.0.1:8000/mcp/`）**：`handoff_current_work` 的 `source_id` 是 **handoff-boundary 边界源专用 id**，必须与 `capture_content_source` 用的 id 不同，否则 409 `source_conflict`；claim 规则：`declared` 不得带 evidence、`verified` 必须带（违反即 422）；prepare 返回的 prepared 对象**原样**传给 `commit_handoff`，成功返回 exact revision（2026-09-11 为 `handoff/handoff#13`，2026-09-12 为 `handoff/handoff#14`）。HTTP wire 细节（2026-09-12 实测）：citation 的 `source_ref` 用 `{name, source_id}`（`name`="content"，不是内部模型的 `source_type` 字段名）；`current-work-handoff` 对象需显式 `"trust": "untrusted_input"`；`notifications/initialized` 返回空 body 属正常；422 的 `details.errors[].loc` 会直接指出缺失/多余字段，按 loc 修参即可。
40. **PowerContext scope 以 `git:github.com/senningi47/commerce-analyst-agent` 为准**（4 个文件 6 处一致 + `list_memory_entries` 实测返回 1 条 active memory）。任何会话接手时先用 `list_memory_entries` 验证 scope 再写入，防止 09-05 那种「找不到记忆」重演。

### 6.6 Gate P preflight 本轮新增（2026-09-12 深夜，Claude Code）

48. **供应商模型会改名，能力快照必须可重冻结。** `deepseek-v4-flash` 于 2026-09-10 退役（由 DeepSeek-V4.1-Flash 服务、按 Flash 价格计费），响应回显改为规范名 `deepseek-flash`——我方网关 fail-closed 拒绝是**正确行为**。能力验证走「免费侦查（GET /models、官方文档）→ 一次最小付费形状诊断 → 快照重建 → 一次验证」。官方明确：不要对响应 `model` 字段做硬编码假设。快照驱动架构使网关代码零改动。
49. **目录级 `git add` 会把从未入库的历史文件卷进窄主题 commit。** 本次 `tests/unit/scripts/` 整目录 add 带入 12 个 Day 2-3 遗留未跟踪文件；处理：message-only amend（未 push、本人分钟级提交）+ 向用户披露 + 执行日志记录。allowlist 纪律应精确到文件。
50. **判断 fake/桩代码死活，必须以被测方的真实调用面为准。** 只 grep 测试文件内的断言引用、漏查被测源码的调用点（FakeConnection.commit/rollback 被 `_postgres.py` 真实调用），会误删——靠试跑抓回。
51. **付费迭代纪律经受了实战检验**：第 6 次探针被 auto-mode 分类器正确拦截（超出逐次授权），转向用户打包授权（诊断→修复→验证循环 + 明确费用上限）后才继续。账本预扣 + 每次硬上限 $0.01 是有效的资金护栏。

### 6.7 Preflight 零付费收尾新增（2026-09-13，Claude Code）

52. **凡解析外部工具输出的代码，其测试样本必须来自该工具的一手真实输出。** 本次 db-check 解析正则被输出头部 `127.0.0.1` 假阳性（databases=127），且单测合成输出格式（`Expected databases: 22`）与真实输出（`Total Databases: 22`）完全不符——双缺陷叠加、离线全绿，只有 live 实跑暴露。坑 2/坑 50 的又一变体。
53. **Windows 下子进程 env 白名单必须包含 TEMP/TMP/USERPROFILE/LOCALAPPDATA/SYSTEMROOT 等运行时变量**，否则 uv/Python 把临时目录解析到 `C:\WINDOWS\`（os error 5），报错完全不指向真实根因。白名单按「运行时需要什么」设计（`_checker_env()`），并用假密钥注入测试锁定剔除语义。

### 6.8 Task 13 主运行本轮新增（2026-09-13 晚，Claude Code）

54. **build 成功 ≠ 镜像可运行；服务起动 ≠ 服务可用；起动可用 ≠ 首轮可用。** 本轮六个缺陷分布在这三层的每一层，且全部「离线全绿、首次真实暴露」。凡「首次真实 X」前先落可观测性（如 server 边界消毒日志），否则每个缺陷多烧一轮授权。
55. **严格 DTO 加载共享配置目录是错的形状。** 评审格式文件（含 evidence 元数据）与运行时窄模型是两个合法层：上移 Config 类作规范加载缝（子类实例即基类实例），不要放松运行时模型、也不要手剥字段。
56. **模型工具目录必须与端口允许名单同源校验。** 合成目录/替身与官方契约重名是巧合不是契约——新守卫 `tests/contract/test_bird_tool_catalog.py` 钉死 BIRD 目录 ⊆ 冻结契约 actions。工具名跨 profile 共享是官方设计（a/c 共用 ask_user/submit_sql），唯一性按 profile 查重；禁词表对官方词汇（knowledge）让位并留注释。
57. **拒绝/降级路径必须携带原调用身份（call_id/name）。** 官方 before_tool_callback 语义是"替换该调用的结果"；另发一条无主结果（`call_id="budget_gate"`）会让 ToolExchangeGroup 封闭校验炸成 400。
58. **Windows 下停止父进程不保证杀尽 detached 子进程；付费运行的止血验证 = 双时点快照对比**（文件计数/成本两拍），不能凭停止回执当已止血。另：按文件名 glob 得到的是字符串，`Path.glob` 才是 Path；监控/聚合脚本先对历史窗口做边界过滤。

### 6.9 Task 8 Step 2/3 本轮新增（2026-09-14，Claude Code）

59. **uvicorn 0.52 在 win32 硬编码 `ProactorEventLoop` 工厂**（`use_subprocess=False` 时 `asyncio_loop_factory` 直接返回 Proactor）——`asyncio.set_event_loop_policy` 与 ASGI factory 内设置**都太晚**（loop 已建），psycopg async 拒绝 Proactor。唯一干净接缝：启动器自管 `asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)` 驱动 `Server.serve()`（`scripts/run_api.py`）。凡「首次真实服务起动」都会暴露此类循环/装配差异（坑 54 再验）：测试全绿 ≠ 服务可起。
60. **vite 7 dev server 默认只绑 IPv6 `::1`**——`127.0.0.1:5173` connection refused（白页，标题即 URL 是失败页特征）。用 `localhost` 访问。另：vite 代理对上游断开的传播有延迟（后端已杀，浏览器 SSE 短时仍显示「已连接」）——断连显示时效在 dev 代理层，直连无此层；重连恢复语义不受影响。
61. **React 19 dev StrictMode 双挂载会把事件流回放双份入 state**（effect→cleanup→effect 竞速）——服务端 Last-Event-ID 重放语义正确也不能免；客户端按事件 cursor 去重是必要防线（修复后 10/10 精确）。
62. **`', '.join(<str>)` 对字符串是逐字符连接**——migration 列元组误写为单字符串时 `CREATE VIEW` 收到逐字符列名（`SELECT e, x, p, ...`）。alembic 事务性 DDL 整体回滚、目录零漂移（护栏按设计工作）；migration 失败停止→修源→重跑，与坑 7/8 一致。

### 6.10 Task 10 本轮新增（2026-09-14 深夜，Claude Code）

63. **同题双模式并发会撞官方 task DB 命名。** 官方 `create_task_db` 的库名只含 task_id（`{base}__{task_id}`），不含 mode——同题 c+a 并发时两集同时对同一 task DB drop/create，`createdb --template` 500（init 阶段即死、零模型调用）。Task 13 两模式异题故从未触发。**同题对照实验必须串行**（concurrency=1 或分时），Task 10 以新 experiment 字母后缀 + concurrency=1 补跑解决。
64. **「离线全绿」不覆盖 Docker 镜像层：agent 侧代码/配置变更后必须 rebuild + 容器内实证。** Task 5 的闸与 v3 配置离线全绿，但 `bird-system-agent` 镜像烘焙于 Task 13 期（configs 挂 BIRD_CONFIG_ROOT 无卷、代码 COPY 入镜像）——live 跑的仍是 Pilot 时代行为（60 ask 失控精确复现）。判别方法（零成本）：容器内 `grep` 新代码符号 + `grep` 配置 revision。§16 清单的「离线全绿」前置条件因此升级为「离线全绿 + 镜像重建 + 容器内实证」。坑 54 的「凡首次真实 X」再变体：这次缺的是「部署证据」而非「可观测性」。
65. **agent 容器的实验身份是 compose 默认值，spool 导入 join 会断。** `BIRD_EXPERIMENT_ID` 未随 run 注入，spool 标记恒为 `bird-system-agent`，`(experiment_id, task_id, mode)` join 找不到 attempt——Task 10 以确定性改挂（唯一候选 + 时间窗 + task/mode，账本披露）绕过。Day 7 修复：Runner 起 run 时把实验 id 写入 compose env。另：**每集一个新 experiment id（字母后缀）是既定先例**（Task 13 a/b/c/d、Task 10 b/c），failed 终态不重跑由 store 契约保证。

### 6.11 能力验证轮新增（2026-09-15 晨，Claude Code）

66. **官方 c 模式每轮新建 agent 会话、上下文仅 phase record。** spool 实证：c 集 16 轮 = 16 个会话各恰 1 轮（sequence 均从 0），a 模式为单会话连续。**任何「在会话内累积记忆」的 c 模式策略假设都不成立**；跨轮信息只能由 phase record 携带——本轮实证 record 未携带任务问题（第 0 轮后 agent 失忆、占位符提交）。修复前先对冻结官方 allowlist 静态核验 record 语义（我方复现缺陷 vs 官方本义）。
67. **栈冷启动窗口内官方库可能不可达**：compose 起动后立即跑 run，sim 两次 "Could not load schema: All connection attempts failed"（官方库 5433 就绪延迟）。**preflight 清单加一步：db-env/官方库就绪探测后再起 run。**
68. **编排器子进程 stderr 无落盘**：`official_task_error` 的具体报错不可追溯（runner 事件只有分类）。Day 7 修复：官方 executor 落盘子进程诊断输出（agent 可见内容，非 GT）。
69. **runner 摘要的 completed 计数含 failed 终态**：`attempted=2 completed=2` 不代表双成功——判定成败以 events JSONL 的 `attempt_finished.status` 或 eval 库 `status` 为准（本轮 c 集 failed 被 summary 掩盖，靠事件流纠正）。

### 6.12 C1 再验证轮新增（2026-09-15 午，Claude Code）

70. **「修复」必须覆盖状态机的全部边界，不只主路径。** `_phase_content` 修复验证了 clarify 循环（单次 run_session 内），但 submit 失败反馈会开启**新 Phase**——局部 `dialogue` 清空、首条消息（任务问题）无跨调用持久化，失忆句式在失败反馈轮精确复现。同类修复先枚举「哪些入口会重新进入该函数」（clarify 轮 / 失败反馈轮 / 新 Phase），逐一写 RED 测试。判据「零失忆句式」必须包含失败反馈轮的对话回放。
71. **Windows Git Bash 会把容器内绝对路径 `/app/...` 改写为宿主路径（MSYS 路径转换）**，`docker compose exec <c> grep ... /app/...` 静默找不到文件——前缀 `MSYS_NO_PATHCONV=1` 禁用转换（本轮实证：裸跑报 `D:/Git/app/...: No such file or directory`）。
72. **fail-closed 解析路径必须自带插桩，否则「不可追溯」会变成连续付费盲烧。** `provider_response_invalid` 已三现（Task 10 Run 1 旧镜像 + C1 重验 b/c 新镜像），每次只剩 sanitized reason、原始响应不留存（Day 3 设计）→ 精确触发（截断 arguments / 回显不匹配 / finish_reason 未知值）无法离线定性，每次复现都要烧一次付费运行。凡新增「拒绝/解析失败」分支，落盘 sanitized 元数据（finish_reason 字符串、异常类、raise 位置）应与分支同批交付。
73. **GC7 补跑只对「瞬时」成立；同因连续两次即转系统性，立即停。** b（97s）失败 → 补跑 c（65s）同因——第二次失败把「赌瞬时」路径关闭，继续跑就是盲烧。停止点应在第二次失败，不在第三次。
74. **`provider_response_invalid` 根因 = 输出预算耗尽，不是协议/语义问题**（插桩一手证据）：reasoning 吃满 `max_output_tokens`（8192）→ finish_reason=length + content 空串 → `FinalOutput(content='')` pydantic `string_too_short` → ValidationError 是 ValueError 子类，被网关 `except (ValueError,…)` 吞成不可重试 protocol error。**给「thinking 开启」的 profile 留输出余量**（现 16384）；凡「成功轮 completion 贴近上限」就是崩溃前兆（晨跑 7133/8192 已亮灯）。另：pydantic 模型的 ValidationError 会伪装成 ValueError 被宽 except 捕获——校验炸点与协议错误要分开定性，插桩日志里的异常类名是关键线索。
75. **「调大 max_tokens」必须对照官方等效水位，而不是「翻倍看看」。** run e 证明 16384 仍会被单轮 reasoning 吃满——官方 ADK 不覆写 max_tokens，provider 思考默认输出 **64K**（max effort 128K，更名探针双源核验）才是等效水位；低于它的任何上限都让偶发 blowout 保持致命。判别「provider 钳制」与「模型真跑满」只需失败响应的 usage.completion_tokens——**插桩日志一开始就该带 usage 字段**（本轮补）。
76. **文本轮不是错误。** 官方 ADK 的 runner 调用遇非工具调用响应（finish=stop/length/content_filter 且有文本）会**正常结束本轮**，编排器按 phase 状态机继续；我方 responder 曾把它当契约违约 fail-closed 400 杀整集（run f）。c-mode 三种模型响应形态都要有官方语义的归宿：工具调用 → 执行；文本 → 结束本轮 + 记忆保留；空内容 → 网关校验拦截（这才是真正的协议级失败）。

### 6.13 A4 a 批轮新增（2026-09-16，Claude Code）

77. **a-mode 长对话的强制上下文可以超 64K prompt 预算（`ContextBudgetExceeded` → agent 500 → official_task_error）。** 2/225 ≈ 0.9%（fake_account_24 第 8 轮、sports_events_8 第 10 轮），对话依赖、任务级确定性（同配置重试同位复发）、非 family 级（同库他集成功）、非瞬时（GC7 补跑无意义只会再烧 ~$0.07）。64K prompt = 128K 窗口 − 64K 输出余量（runs d/e 冻结修复），抬 prompt 上限必复发 reasoning 爆炸——修复风险大于 2 集损失，裁定不修、如实标注。判别入口：容器日志 `boundary 500` + `ContextBudgetExceeded` traceback（stderr 落盘只有 httpx 500 客户端侧）。
78. **「离线全绿」不覆盖运行参数的冻结面：改任何被 pydantic/守卫钉死的参数前先 grep 约束。** 并发 2→4 的指令在 `RunnerConfig` 冻结 cap（v0.3 §16.2）处被拒（b08 首射零消耗失败、events 都没建）。规格留有条件升档口（「错误率不升且 P95 明显改善」）；升档 = 一行 + 钉测试 + 试点批活体验证 + 披露。另：TaskOutput 阻塞等待 10 分钟硬上限 vs ~40 分钟批次 = 正常轮询周期，不是运行故障（完成通知机制兜底）。
79. **c 模式 spool 是每模型轮一个文件，导入器「1 session = 1 attempt」假设对 c 格欠记 ~4×。** 消融首次导入 assigned=120 但 156 记录 unassigned——多记录组窗口匹配只消费首个；A/c 实测 DB $0.0262 vs 真值 $0.1075。修正 = 导入侧按 (experiment,task,mode) 预聚合为集级记录再归属（merge_telemetry 是 jsonb 顶层键替换，重跑安全）；修正后 DB 四格与 spool 聚合逐分一致。判别信号：unassigned 计数 > 0 且同 (task,mode) 多 session。c 批当年只做 spool 聚合未做 DB 导入，不受影响。
80. **同题双模式的 Runner 清单必须按模式拆分串行。** 60 集 c+a 混合清单在并发 4 下被坑 63 守卫直接拒绝（同 task 跨 mode 并发撞官方 task DB 命名）；拆 c-30/a-30 两份串行即可。另：shell `&` 发射长任务后必须按坑 58 双时点核验存活（events 增长 + 库内行数 + 容器存在）。


## 7. 关键文件与 SHA-256

哈希用于发现意外变化。入库状态（2026-09-12 晚）：§7 全部所列源码/测试/文档**均已随 14 个 commit 入库**（Day 4 allowlist → checkpoint 补录 → Day 5 Phase A 十二连）；`HANDOFF.md`、`CLAUDE.md` 本身为收尾更新、保持未跟踪。2026-09-11 由 Claude Code 现场计算并机械复核（15 组报告哈希对 + HANDOFF 交叉核对全部一致）；2026-09-12 复验 11/11 MATCH。

| 文件 | SHA-256 | 状态 |
|---|---|---|
| `db/migrations/versions/0004_day4_product_operations.py` | `b1fba3d574c0251848b6778f4a7527a5fda32fbfc44e8a585877cb351967e498` | 不变（09-07 基线） |
| `src/commerce_agent/operations/_postgres.py` | `437bd1efba4f0894f9b9230e84b16da82671682548515100e45b3ea1c3d77782` | 不变 |
| `src/commerce_agent/operations/_seller_refs.py` | `1060607eba93c11f856eaf235a60eda2e797867f49e3583d55f216f16499e86b` | 不变 |
| `tests/unit/test_day4_operations_migration.py` | `80b4566ae8fa1b6cb566c72a10bc632c2dbc1749479e188291e8c4e5756df822` | 不变 |
| `tests/unit/operations/test_postgres_adapter.py` | `4843394b4bd10501214a7ab0fa8c59240b7dd95af891882c985d837d60377208` | 不变 |
| `tests/unit/operations/test_seller_refs.py` | `f58b4e53ba75b98f19116d8333dbb300f2717396f85be7e282db220b1a62b731` | 不变 |
| `tests/integration/product_eval/test_seller_risk_postgres_scenario.py` | `d67589266c951d3ce23d97f0a6bdfa56b49e1f5db2b5b2185ebe5292e6de9705` | 不变 |
| `tests/integration/checkpoint/test_retail_postgres_resume.py` | `eb509faaadd44854b064a217ff47536f09a733043edcc6401ad6bbe1c88fbb08` | **Task 18 修改** |
| `tests/integration/checkpoint/test_checkpoint_privacy.py` | `63605191f54b4dfc6a051e070376bd8e741b8574ae51f6b2fa3aa84227aaf18a` | **Task 18 修改** |
| `docs/reports/2026-09-06-day-4-product-closed-loops.md` | `203995a8537c430b7a322c782db0761ed700e50aedddb3016ec74c203eef1398` | **新建** |
| `docs/reports/2026-09-11-task18-execution-log.md` | `81b99e2462ae844a98854eabff659987420c76e4c33efa3b8fc93db3b515fd80` | **新建** |
| `docs/superpowers/plans/2026-09-12-day5-bird-contract-and-evaluation-runner.md` | `34d988fa6008fa30b22954efb930d9d4f293f33b929ed58f921d5d6afdbc811a` | **新建（2026-09-12，已批准）** |
| `docs/reports/2026-09-12-gate-g-cleanup-day5-plan.md` | `591bf766388cd461495835e5d7a9b2e4011c8b1b781a6f6bbb873c55759084d9` | **新建（2026-09-12，含 §7 补录追加后的终版）** |
| `docs/reports/2026-09-12-day5-phase-a-implementation.md` | `739143364e18a5cd681dd6f9f7f87550077405c091723e97ecc62d37accf1d5a` | **新建（2026-09-12，Phase A 执行日志）** |
| `docs/project/specs/2026-09-12-day5-gate-p-pilot-authorization-request.md` | `17604ee50a510416b5cea5afd8ae216d2ff98a17de7990a950f90425271c607e` | **已批准（2026-09-12 裁定：30 元 + 打包 preflight）** |
| `configs/model/deepseek-flash-capability.v1.json` | `6c826461d3c348ea5aee4ee5e727f46c5cca2e8ee325bf2a6b1af7b293385769` | **新建（2026-09-12 深夜，快照 v4）** |
| `configs/model/deepseek-flash-price.2026-09-12.json` | `994b762994662a2ea03b7ee42c0008ed35d3f953cd22e262601a3c46621760ae` | **新建（2026-09-12 深夜，双源核对价格）** |
| `docs/project/research/2026-09-12-deepseek-flash-rename-capability.md` | `71212afed7f5b9caeeaa3c26ce6e17ff5390b2c8c085eb4e1460f5cd10ce067c` | **新建（2026-09-12 深夜，更名证据）** |
| `docs/reports/2026-09-12-gate-p-preflight-execution-log.md` | `fb5a07b91fa89b4efc3e608cca482c2b089b81284f07f53fd2d8e2c8fd84c89c` | **新建（2026-09-12 深夜，§2.7 执行日志）** |
| `scripts/prepare_bird_pilot.py` | `471b4c3997de9fb9e53abed4899fe834e63c9fb7d770b964bb75b8d2e322762e` | **修改（2026-09-13，三缺陷红绿修复；未 commit）** |
| `tests/unit/scripts/test_prepare_bird_pilot.py` | `93a0c188e99646189765fe137635f4ff6dc4cbbd750f1b5b59dd1f458d95f91f` | **修改（2026-09-13，+2 测试与真实输出样本；未 commit）** |
| `outputs/bird-pilot/task-selection.json` | `b18897771f773cd5e025d9876673b3ba95044add33fe8e590669f38ffb5f9017` | **新建（2026-09-13，20 题公开选取清单）** |
| `docs/reports/2026-09-13-preflight-zero-cost-completion.md` | `666fbf2399d1980c05810844591c595c5a0a352305ff549a74a5c64bc78bc800` | **入库（4ff1195；§7 原记 3e826a54… 为收尾期后补编辑前的旧值，已核实现值与内容自洽）** |
| `src/commerce_agent/model/snapshots.py` | `7383a2e593d3e59dbb403912ce3976a5475c1047df47bc8e3e762f857af574df` | **新建（2026-09-13 晚，评审快照加载缝）** |
| `configs/model/run-profiles.v2.json` | `f9cd5558570890b9d12d642bf85d30cc185d925518e50681ad676d4266af8191` | **修改（2026-09-13 晚，bird_a 规则换官方 9 工具）** |
| `configs/model/bird-tools.synthetic.v1.json` | `d9b3590f05a8f75bee26fd6472c4c0c3cbe1d7ca8d0a7cca05e8cbcbfee37306` | **修改（2026-09-13 晚，bird_a 目录换官方 9 工具；bird_c 不变）** |
| `bird_system_agent/requirements.txt` | `e1c4095d13619170a04e1bbe224c11701543a070930817c3e0b711461337b5ab` | **修改（2026-09-13 晚，重冻结 45 包）** |
| `docs/reports/2026-09-13-task13-pilot-main-run.md` | `4bc8b33711d21378fafc73922def64abc32ea7a1664a68f3eaff04a35d8cc023` | **新建（2026-09-13 晚，Task 13 执行日志）** |
| `outputs/bird-pilot/task-list.jsonl` | `f722e671a7afc4d91a358e655c212b336d766b797930368f2e21b1eaa5b634f2` | **新建（2026-09-13 晚，Runner 任务清单，公开字段）** |
| `docs/reports/2026-09-13-task13-pilot-report.md` | 待 commit 时现场计算 | **已入库（3a71357）** |
| `docs/superpowers/plans/2026-09-14-day6-capability-restore-runner-and-ui.md` | 待现场计算 | **已入库（b1c9a4e，已批准）** |
| `docs/project/research/2026-09-14-submit-semantics-alignment.md` | 待现场计算 | **已入库（1bdcf78）** |
| `src/commerce_agent/evaluation/spool_importer.py` | 待现场计算 | **新建（4ceab5c）** |
| `scripts/probe_model_capability.py` | 待现场计算 | **新建（636129c）** |
| `docs/reports/2026-09-14-day6-phase-a-execution-log.md` | `1cef9100af66d8c4d8b5a1b3e04a554f45a27225c1510786711ad66c4921bf9a` | **新建（de66c21，Task 1–3）+ Task 4 追加（§6/§7，待 docs commit）** |
| `src/commerce_agent/orchestration/retail_graph.py` | `9283c8b36cf35560b866e0026cca626bab7be8ae4156a0f7a99b273307852a91` | **修改（84180e2，Task 4 图 allowlist 对齐 v2 规则）** |
| `src/commerce_agent/orchestration/bird_c_responder.py` | `2c809883268c9128c498642332cb54576cf234dbfc245eba66940787321e798a` | **修改（84180e2，Task 4 常量提升；⚠ 未跟踪文件，从未入库）** |
| `tests/contract/test_bird_tool_catalog.py` | `757714a24799836d75fcc57de504c2fc2b440582bd5f4fe9f09ffa7cee2a5dab` | **修改（84180e2，Task 4 三方一致性守卫 2→6 测试）** |
| `tests/unit/orchestration/test_retail_graph.py` | `df647d62c8a422e02ada4351ef8533faf98ae0352e605742d8371e2f8701d6cd` | **修改（84180e2，Task 4 legacy 测试对齐 + 死代码清理）** |
| `scripts/probe_deepseek_gateway.py` + `tests/unit/scripts/test_probe_deepseek_gateway.py` + `tests/integration/deepseek/test_retail_probe.py` | — | **删除（84180e2，Day 3 旧探针退役；前两个 tracked git rm，最后一个未跟踪磁盘删除）** |
| `configs/model/prompt-policies.v3.json` | `c8dfc65f5d52186827233f6260b7806fc786560810284be2e6585dcf4ddd66f3` | **新建（ad961e8，Task 5 bird-a/bird-c 官方策略整合；v2 文件保留作历史）** |
| `configs/model/run-profiles.v2.json` | `45b3d308893a7053c6fe7e1fc21f791fa85ffd697d19f4848c53aa19f073f573` | **修改（2.28 输出预算：bird_a/bird_c 推理 16384 + profile revision v2，canonical 重写；未 commit。前值 d231b616… = ad961e8）** |
| `src/commerce_agent/context_builder/profiles.py` | `81fd32aeb800e0ed9a0fd1d2fc14091c1f6a074729c6f2a19cadff35d3c9f5c8` | **修改（ad961e8，_FILES/revision 校验/_POLICY_REVISIONS 切 v3）** |
| `src/commerce_agent/orchestration/bird_server.py` | `9aab08b44487d29e8abd0fe7a92641b67094c35527cb859be3ffce44993ea26f` | **修改（2.26 残余修复：Phase 边界会话记忆，未 commit；前值 3569b4b7… = ad961e8 闸修复）** |
| `tests/contract/test_bird_prompt_policy.py` | `86fa18b0fe0f84431e649b11a1e2967a85e8b8c565b6fc4748adfca91dbfc3ea` | **新建（ad961e8，策略守卫 6 条）** |
| `tests/contract/test_bird_system_server_adapter.py` | `112fb706d5b5ce62fea3ee8f2e9570865d0df8342db46335027de67cc676a470` | **修改（2.26 残余修复 +2 测试，未 commit；前值 f2f016bb… = ad961e8 gate 测试）** |
| `tests/unit/context_builder/test_profiles.py` | `a0e4553ae2b9c6ba43b6a05954aa4073c4b8c1a23241b09f4de878ce51b869eb` | **修改（ad961e8，canonical 校验文件名 v2→v3）** |
| `tests/integration/evaluation/test_runner_sigint_recovery.py` | `f3b4ba5ca399f083dce8f638814785eb021974e1ff65b7d0f9dbf00ae9cb47e7` | **新建（f6af9f1，Task 6 SIGINT 恢复演练，验收门② PASS）** |
| `db/migrations/versions/0006_day6_trace_read_view.py` | `f344c0e7ae1ae74a0eb7ef12fd5ac9e452540e5c15782d3b076e060992573d34` | **新建（3003ca5，Task 7 ops_read 只读视图；已应用）** |
| `src/commerce_agent/api/events.py` | `8481be725f198b6c8cd732a12c68e76ce08649a69b1543f33e5666f18c21ad40` | **新建（3003ca5，稳定事件 schema + 白名单 + 游标）** |
| `src/commerce_agent/api/sse.py` | `48f62976dbbf2dd1bcda1a49262617645123713750a0e42cbcdcc753a5f8ad08` | **新建（3003ca5，PG 事件源 + SSE 生成器 + 路由）** |
| `src/commerce_agent/api/app.py` | `57f8d22f111ea2b9d77cebcf2f2a37bf7a53408c0a0863b96db6bb71b9850398` | **新建（3003ca5，FastAPI 工厂）** |
| `tests/integration/api/test_trace_sse_pg.py` | `f5c52c19e0b9b65241b644aeac2aba6b1b088c594012dbefd3d038f22c8665ee` | **新建（3003ca5，PG 判据 3 条）** |
| `tests/unit/api/`（test_events.py `1208e95e…` / test_sse.py `c0de728b…`）+ `tests/unit/test_day6_trace_view_migration.py`（`bc42d7b9…`） | 见左 | **新建（3003ca5，离线 11 条）** |
| `web/`（package.json + lockfile + src/ 10 文件：App/Workbench/ApprovalsPage/EvalCenterPage/ResultChart/types/view/mock.stream/styles 等） | 详见 `79de29b` + `7d3881e`（两 commit） | **新建（Task 8 Step 1 Demo，用户已确认布局；独立 npm toolchain）** |
| `db/migrations/versions/0007_day6_eval_read_view.py` | `5e9a9d581d662c24f440c5d576731cb45fd9bbfb53a90e1d8038aa46a210ca84` | **新建（Task 8 Step 2/3，eval 只读视图；已应用，head）** |
| `src/commerce_agent/api/eval.py` | `5de358da26636be20fd97a64ea69de623f3df3f647bcf5b4adb7024a016d81b8` | **新建（Task 8 Step 2/3，只读 eval API）** |
| `src/commerce_agent/api/runs.py` | `660963578ff64eb540b0d4f58698fa12b7ef395c4ef1c167a90360a51ec697dc` | **新建（Task 8 Step 2/3，运行目录 API）** |
| `src/commerce_agent/api/app.py` | `c6e4a6ae5d978a7aadad8e1b048ef64ea5a91f087172abbc4146d1a994c741a9` | **修改（Task 8 Step 2/3，可选源 + create_postgres_app）** |
| `scripts/run_api.py` | `12e1875d41f33b0243acd0ac57800a209e0117df432e3c714f43bdf200a2e51a` | **新建（Task 8 Step 2/3，win32 selector-loop 启动器，坑 59）** |
| `scripts/seed_ui_live_run.py` | `eeca09a99f5f943eee70902caa90c90cd5fff2e3c8b88492dacd240fcbae3fed` | **新建（Task 8 Step 2/3，一次性浏览器验证种子；用后须场景 reset）** |
| `tests/unit/test_day6_eval_read_view_migration.py` | `24667d2982c6f0787761a2ec5d3600dde5f030791e71a2035f38a40820d434e0` | **新建（Task 8 Step 2/3，migration 源测试 5 条）** |
| `tests/unit/api/test_eval_and_runs_api.py` | `375869fbef40f97bc559ac88ebd8e70c96eca21a4768648c8bed5ec7a81b6f85` | **新建（Task 8 Step 2/3，api 单测 6 条）** |
| `tests/integration/api/test_eval_read_pg.py` | `7d7d6fb38b667157883ba36a65972035c71330ba07ebe726f802adb2bef1146c` | **新建（Task 8 Step 2/3，PG 判据 5 条含负向 ACL）** |
| `docs/reports/2026-09-14-day6-phase-a-execution-log.md` | `d52323ac24a3f33121fdc99d8e6d58c3cc590989807ec3f1749d6ef67f308888` | **修改（Task 8 Step 2/3 追加 §14）** |
| `web/src/`（api.ts 新建；App/view/types/Workbench/ApprovalsPage/EvalCenterPage/mock.stream/styles/vite.config 修改） | 详见本轮 commit | **修改（Task 8 Step 2/3，SSE 客户端 + live 模式 + 评测中心真实数据）** |
| `pyproject.toml` + `uv.lock` | 详见本轮 commit | **修改（uvicorn>=0.30,<1 入依赖）** |
| `tests/e2e/test_product_chain_api.py` | `b9ab7c2a20479e98e2b5c0eae679f93a001c6ad02ec85ad6612244208b982fdb` | **新建（Task 9，PG E2E 3 条：工作台链路 REST+SSE / 三闭环审批执行读回 / 评测中心读回 + 载荷白名单）** |
| `tests/e2e/conftest.py` | `a63b8d09c1e1bdb42633d7cd82283ee748ce785f20a3ae9ba188760059cfec49` | **新建（Task 9，selector policy + postgres skip 门控）** |
| `tests/e2e/test_ui_acceptance.md` | `f51fd011bcc1f690e9a6bedef0628a6e5aac2d675b9292a4244189ddd834b891` | **新建（Task 9，UI 手工验收清单 + Playwright 不引入裁定）** |
| `docs/reports/2026-09-14-day6-phase-a-execution-log.md` | `1bfde99757debb8d298ae5107d6c7bb50778261ee045ee457b21e7c79d036d96` | **修改（Task 9 追加 §15；§14 哈希 d52323ac… 为追加前值）** |
| `docs/project/research/2026-09-15-full-redesign-options.md` | `6785b21fa5db14452a706fff3fde2b0153a0a1363e304329410666ef0e8e2a5d` | **新建（6ef0c91）；后追加 §11 回填（2b6c6c26，现值见 git）** |
| `docs/superpowers/plans/2026-09-15-day7-cmode-fix-a4-and-closure.md` | `2b6c6c260fbd4fdf511a1d8da07c663e1c2bd9e7320ab2dfa321b33ae595ecb8` | **新建（2b6c6c26，Day 7 计划，待用户批准）** |
| `docs/reports/2026-09-14-day6-phase-a-execution-log.md` | `6ae295645d70bdab867441b4ce854b77d556d9f3ad589604961e087ad8a5df42` | **修改（Task 11 追加 §18；前值 1bfde997… 为追加前值）** |
| `outputs/bird-pilot/task11/task-selection.json` + `task-list.jsonl` + `task-list-c-rerun.jsonl` + `.gitignore`（+1 行 task11/task-data/） | 见能力验证产物 commit | **新建（2026-09-15，seed 13 选题与 Runner 清单；task-data/ GT 拆分 gitignored）** |
| `docs/reports/2026-09-14-day6-phase-a-execution-log.md` | `c59e959129eaa4106e09cd19c4a002226725ba4b3cb07e48316ba036bdcb38e2` | **修改（能力验证追加 §19；6ae29564… 为 §18 后值）** |

不要覆盖或回退这些文件。若现场哈希不同，先确认是否是用户或其他会话的新修改，再继续工作。

## 8. 本轮（Task 13 主运行会话，2026-09-13 晚，Claude Code）实际修改文件

**已入库（均经用户裁定；未 push）**：
- 开场 Gate G `4ff1195`：`scripts/prepare_bird_pilot.py`、`tests/unit/scripts/test_prepare_bird_pilot.py`、`outputs/bird-pilot/task-selection.json`、`docs/reports/2026-09-13-preflight-zero-cost-completion.md`、`HANDOFF.md`、`CLAUDE.md`（6 文件，+1150/−17）。
- `4530cb5`（26 文件，+1767/−237）——六活体缺陷修复（红绿，829 passed / Ruff 全绿）：
- `bird_system_agent/requirements.txt`（重冻结 45 包；psycopg[binary]）
- `scripts/spikes/bird-user-simulator.Dockerfile`（官方栈实测 freeze 全钉 + sqlglot==30.17.0）
- `src/commerce_agent/model/snapshots.py`（**新建**：评审快照加载缝）、`scripts/snapshot_deepseek_model_config.py`（导入再导出）、`bird_system_agent/runtime.py`（加载缝 + marker 校验 + provider_user_id fail-fast + `"0"*32` 默认）
- `bird_system_agent/Dockerfile` + `.dockerignore`（契约 fixture 入镜像）+ `tests/contract/test_bird_gt_isolation.py`（守卫同步）
- `configs/model/bird-tools.synthetic.v1.json`（bird_a 官方 9 工具）+ `configs/model/run-profiles.v2.json`（规则同步）+ `tests/contract/test_bird_tool_catalog.py`（**新建守卫**）
- `src/commerce_agent/context_builder/profiles.py`（按 profile 查重 + 禁词调整）、`src/commerce_agent/orchestration/tools.py`（替身 get_schema 分支）、`tests/unit/context_builder/test_profiles.py`、`tests/unit/orchestration/test_track_isolation.py`、`tests/fixtures/orchestration/bird-tools.synthetic.v1.json`（同步）
- `src/commerce_agent/orchestration/bird_server.py`（预算门身份保持）+ `tests/contract/test_bird_system_server_adapter.py`（断言补强）
- `bird_system_agent/server.py`（边界消毒日志）+ `tests/unit/bird_runtime/`（**新建**：__init__、test_bird_runtime_factory.py、test_server_boundary_logging.py）、`tests/unit/model/test_reviewed_snapshots.py`（**新建**）
- `pyproject.toml` + `uv.lock`（python-dotenv==1.2.3、pydantic-settings==2.15.0——宿主侧 orchestrator 子进程依赖）
- `compose.bird.yaml`（provider_user_id 默认 32 零）

**未入库（Pilot 产物与收尾文档）→ 已随 `9f4df34` 入库（4 文件，+171/−35）**：`outputs/bird-pilot/task-list.jsonl`（公开）；`docs/reports/2026-09-13-task13-pilot-main-run.md`（执行日志）；本文件与 `CLAUDE.md`（§0/§2.9/§3-§8 刷新）。
**gitignored 产物（不入库）**：`outputs/bird-pilot/task-data/`（GT 拆分）、`outputs/bird-budget/pilot-ledger.json`（已写实测值）、`outputs/bird-agent-spool/`（agent 侧用量原始证据）、`outputs/bird-eval/`（events JSONL、episode 输出、诊断输出——agent 可见内容，非 GT）。

**外部状态**：spike 容器已 stop（6002 释放）；compose 栈三服务 running；eval 库含 a/b/c/d 四实验身份数据；官方 db（5433）与产品 db（5432）未动。未 push。

### 8.1 Task 8 Step 2/3 轮（2026-09-14 新会话，待 commit）

- **新增**：`db/migrations/versions/0007_day6_eval_read_view.py`（已应用）、`src/commerce_agent/api/eval.py`、`src/commerce_agent/api/runs.py`、`scripts/run_api.py`、`scripts/seed_ui_live_run.py`、`tests/unit/test_day6_eval_read_view_migration.py`、`tests/unit/api/test_eval_and_runs_api.py`、`tests/integration/api/test_eval_read_pg.py`、`web/src/api.ts`。
- **修改**：`src/commerce_agent/api/app.py`（create_postgres_app）、`web/src/`（App/view/types/Workbench/ApprovalsPage/EvalCenterPage/mock.stream/styles/vite.config）、`pyproject.toml` + `uv.lock`（uvicorn）、执行日志 §14、本文件、`CLAUDE.md` §0。
- **DB 现场变更（预授权范围内）**：migration 0007 应用（ops_read 两视图 + agent_reader SELECT；基表 ACL 零改动）；浏览器验证用一次性种子场景 `day6-ui-live-v1` 写入后已 reset 归零（seeded trace 0 行）；eval 库 4 实验 = Pilot 原有数据未动。
- **外部状态**：vite dev（5173）与 API（8010）进程已停；8000 = PowerContext（勿占用）。零付费、零 GT 读取。

### 8.2 Task 9 轮（2026-09-14 同会话，待 commit）

- **新增**：`tests/e2e/__init__.py`、`tests/e2e/conftest.py`、`tests/e2e/test_product_chain_api.py`、`tests/e2e/test_ui_acceptance.md`。
- **修改**：执行日志 §15、本文件、`CLAUDE.md` §0。
- **DB 现场变更（预授权范围内）**：E2E 三场景（investigation/seller-risk/alert，`task15-e2e-*-v1`）写入→断言→finally 场景 reset 归零；eval 验证实验自建自清理。三套件终态：PG **140 passed**（integration 137 + e2e 3）、离线 **866 passed, 140 skipped**、Ruff 全绿。
- **外部状态**：无残留进程；零付费、零 GT 读取。

### 8.3 Task 10 零付费准备轮（2026-09-14 同会话，待 commit）

- **新增（公开，入库）**：`outputs/bird-pilot/task10/task-selection.json`、`outputs/bird-pilot/task10/task-list.jsonl`；`.gitignore` +2 行（`task10/task-data/` GT 拆分、`bird-pilot/bird-budget/` seed 账本）。
- **gitignored**：`outputs/bird-pilot/task10/task-data/`（2 个 GT 拆分）、`outputs/bird-pilot/bird-budget/pilot-ledger.json`（Task 10 seed 账本）。
- **DB/付费**：零 DB 变更、零 API 请求（脚本 preflight 完整输出「no API requests were made」）；Pilot 账本核验完好。
- **状态**：付费运行 staged，待空闲档（见 §3 与执行日志 §16 一键清单）。

### 8.4 Task 10 执行轮（2026-09-14 深夜，付费已行使）

- **付费**：3 次有效运行（Run 1 a+c、Run 2 c 补跑、Run 3 c 补跑）——agent 实测 **$0.025047084**（spool 逐轮聚合：0.011539890 + 0 + 0.009389802 + 0.004117392）；sim 侧不可见估 $0.05–0.08，**待用户余额核对终裁**；band 全程 off_peak。
- **DB 现场变更（预授权范围内）**：eval 库新增实验 `task10-strategy-validate-20260914`（a succeeded / c failed-infra）+ `…b`（c succeeded-runaway）+ `…c`（c succeeded-fixed）；Run 3 attempt 的 telemetry 经 Task 2 importer 回填（`merge_telemetry`，14 轮/$0.004117392；experiment_id 从 compose 默认值确定性改挂，披露）。migration 未动（head 0007）。
- **镜像**：`bird-system-agent` 重建（含 Task 5 闸 + v3 配置；容器内 grep 实证）；`bird-user-simulator`/`bird-db-environment` 未动。
- **外部状态收尾**：compose.bird 三服务与官方库 5433 已 stop（回到开场前）；spike 6002 全程 Exited；产品 PG 5432 未动。
- **gitignored 产物**：`outputs/bird-eval/events-task10*.jsonl`、`outputs/bird-eval/episodes/<3 个新 attempt>.json`、`outputs/bird-agent-spool/`（+75 文件：a 1 + b 60 + c 14）、账本 task10 节。
- **新增（公开，入库）**：`outputs/bird-pilot/task10/task-list-b-c1.jsonl`（c 单集补跑清单）、`docs/reports/2026-09-14-task10-strategy-validation.md`（验证报告）、执行日志 §17、本文件、`CLAUDE.md` §0。

### 8.5 Task 11 轮（2026-09-15 新会话，零付费收官）

- **新增（入库）**：`docs/project/research/2026-09-15-full-redesign-options.md`（commit `6ef0c91`）。
- **修改（入库，收尾 commit）**：执行日志 §18、本文件（§0/§2.21/§3/§5/§7/§8.5/头部 revision）、`CLAUDE.md` §0。
- **现场**：零代码变更、零付费、零 GT 读取、零 DB 写、零进程/容器变更（纯文档轮）；未 push。gitignored 账本未动（Task 10 sim 侧余额核对后由用户数字回填）。

### 8.6 能力验证轮（2026-09-15 晨，付费裁定行使）

- **新增（公开，入库）**：`outputs/bird-pilot/task11/task-selection.json`、`task-list.jsonl`、`task-list-c-rerun.jsonl`、`.gitignore` +1 行（`outputs/bird-pilot/task11/task-data/` GT 拆分 gitignored）。
- **gitignored**：`outputs/bird-pilot/task11/task-data/`（2 个 GT 拆分）、账本 `task11_capability_validation` 节、`outputs/bird-eval/events-task11-capability*.jsonl`、episodes 2 新文件、spool +19 文件。
- **付费**：agent 实测 $0.034841（a 11 轮 + c 废弃 2 轮 + c 重跑 16 轮，off_peak 全程）；sim 估 ~$0.022；合计估 ~$0.057（授权 $0.05–0.10 内）。
- **DB 现场变更**：eval 库新增实验 `task11-capability-validate-20260915a/b` 共 3 attempt 行（a succeeded、c failed、c succeeded，证据保留）；migration 未动（head 0007）。
- **外部状态收尾**：compose.bird 三服务与官方库 5433 已 stop（回到开场前）；产品 PG 未动。未 push。

### 8.7 C1 再验证轮（2026-09-15 午，付费 Gate 行使）

- **付费**：1 次有效运行（`task7-cmode-refit-20260915`，c 单集 succeeded 118.4s）——agent 实测 **$0.014710**（spool 5 文件聚合，off_peak 全程）；sim 估 ~$0.0015；合计 ~$0.016（授权 ≤$0.05 内）。
- **DB 现场变更**：eval 库新增实验 `task7-cmode-refit-20260915`（1 attempt succeeded，reward 0.0，证据保留）；migration 未动（head 0007）。
- **源码**：零变更（rebuild 按清单跳过；`4287a0b` 后代码路径 `git diff` 为空，容器内 `_phase_content`×2 实证在场）。
- **gitignored 产物**：`outputs/bird-eval/events-c1-refit.jsonl`、episodes 1 新文件（`a5073903…json`）、spool +5 文件、账本 `task7_c1_refit_20260915` 节。
- **修改（未 commit，待授权）**：执行日志 §21、本文件（头部/§0.2/§2.25/§3/§5/§6.12/§8.7）、`CLAUDE.md` §0。
- **外部状态收尾**：compose.bird 三服务与官方库 5433 已 stop；产品 PG 未动。未 push。

### 8.8 残余修复轮（2026-09-15 午续，零付费，未 commit）

- **修改（零付费红绿，待 commit 授权）**：`src/commerce_agent/orchestration/bird_server.py`（`_Session._task_message`/`_memory` 持久化 + `_phase_content` current_message 渲染槽，哈希 `9aab08b4…`）、`tests/contract/test_bird_system_server_adapter.py`（+2 Phase 边界测试，哈希 `112fb706…`）、执行日志 §22、本文件（头部/§0.2/§2.26/§3/§5/§7/§8.8）、`CLAUDE.md` §0。
- **镜像**：`bird-system-agent` rebuild + 一次性容器内实证（`Orchestrator message for this phase`×1 / `_task_message`×4 / `_memory`×2）✓；run 容器 --rm 自清。
- **判据终态**：适配器 23 passed（+2）；离线 **869 passed, 140 skipped**；Ruff 全绿。
- **DB/付费**：零 DB 变更、零 API 请求；栈未起动（仅 build）。
- **下一步**：C1 重验（`task7-cmode-refit-20260915b`）等用户付费授权。

### 8.9 C1 重验轮（2026-09-15 午后，付费 Gate 行使 b/c 两连败）

- **入库**：`0f99076`（fix：bird_server.py + 测试，+141/−6）+ `bd38072`（docs：执行日志 §21/§22 + HANDOFF + CLAUDE）。`--check` 例外已披露（HANDOFF 头部既有 Markdown 硬换行风格）。
- **付费**：b `task7-cmode-refit-20260915b`（attempt `5aac285d`，97s failed，agent $0.004848）+ c `…c`（GC7 补跑，attempt `316e3ae4`，65s failed，agent $0.003392）——重验授权内合计 **$0.008240** ≤ $0.05；两败同因 `provider_response_invalid`（网关边界日志 503）。
- **DB 现场变更**：eval 库新增 2 实验 2 attempt 行（failed/official_task_error，证据保留）。
- **gitignored 产物**：`events-c1-refit-b/c.jsonl`、episodes 2 空壳、spool +3 文件、账本 `task7_c1_refit_20260915.reruns` 节。
- **修改（未 commit，待授权）**：执行日志 §23、本文件（头部/§0.2/§2.27/§3/§5/§6.12 坑 72–73/§8.9）、`CLAUDE.md` §0。
- **外部状态收尾**：栈与官方库已 stop；产品 PG 未动。

### 8.10 方案1 轮：插桩定性 + 输出预算修复（2026-09-15 午后，诊断付费 $0.008127 + 修复零付费，未 commit）

- **修改（待 commit 授权）**：`src/commerce_agent/model/gateway.py`（插桩 `_log_protocol_diagnostic`，哈希 `886c1517…`）、`src/commerce_agent/model/contracts.py`（ThinkingConfig le 16384，`57849aa6…`）、`configs/model/run-profiles.v2.json`（bird_a/c 16384 + revision v2，`45b3d308…`，§7 已更新）、`tests/unit/model/test_gateway.py`（+2，`c5de626c…`）、`tests/unit/model/test_contracts.py`（+1，`894df49e…`）、`tests/unit/context_builder/test_profiles.py`（+1，`4eee27ef…`）、执行日志 §24、本文件（头部/§0.2/§2.28/§3/§5/§6.12 坑 74/§7/§8.10）、`CLAUDE.md` §0。
- **付费**：诊断运行 d（`task7-cmode-refit-20260915d`，attempt `83b6e31d`，failed 126s）agent $0.008127 ≤ ~$0.01 ✓；DB 新增 1 实验 1 attempt 行（failed/official_task_error，证据保留）；gitignored：`events-c1-refit-d.jsonl`、episode 空壳、spool +3、账本 `reruns.instrumented_diagnostic` 节。
- **镜像**：rebuild ×2（插桩后 + 修复后）；容器实证 `protocol_diagnostic`×4 / `bird-c-profile-v2` / `"max_output_tokens":16384`×2 ✓。
- **判据终态**：**873 passed, 140 skipped**（+2 插桩、+2 修复）；Ruff 全绿。
- **外部状态收尾**：栈与官方库已 stop；产品 PG 未动；14:00 后未再发起任何付费调用。

### 8.11 A4 a 批轮（2026-09-16，付费行使 + 截停，commit 待授权）

- **源码（待 commit）**：`src/commerce_agent/evaluation/runner.py`（concurrency cap le=2→le=4，用户裁定 + §16.2 偏差披露）、`tests/unit/evaluation/test_runner.py`（+1 cap 钉测试）；判定终态 **887 passed, 140 skipped** + Ruff 全绿（v4 的 888 含 3 守卫，回退后 886+1）。
- **配置（无净变化）**：v4 实现期间动过 `profiles.py`/`run-profiles.v2.json`/`test_profiles.py`/`test_bird_prompt_policy.py`/新增 `prompt-policies.v4.json`，验证后**全部 git checkout 回 HEAD**，v4 JSON 归档至 gitignored `outputs/bird-eval/prompt-policies.v4-20260916.json`。
- **新增（公开，待 commit）**：`outputs/bird-pilot/a4/task-list-a-b01…b12.jsonl`（12×25 顺序切片，同批异题）、`task-list-a-v4check.jsonl`（3 集）、`task-list-a-b02-rest.jsonl`（22 集）、`docs/reports/2026-09-16-a4-a-batch-truncated-and-v4-validation.md`、执行日志 §32、本文件、`CLAUDE.md` §0。
- **gitignored 产物**：events ×10（b01–b09 + v4check）、episodes 225+2 失败空壳（含 stderr/stdout 落盘）、spool +225 文件、账本 `a4_a_batch_20260916` + `v4_validation_20260916` 节、`agg_a4a.py`/`import_a4a_telemetry.py` 助手。
- **DB 现场变更（a 批授权范围内）**：eval 库新增 10 实验身份 225 attempt 行（223 succeeded + 2 failed，证据保留）+ telemetry merge 225/225；migration head 仍 0007；产品 PG 业务表零变化。
- **付费**：agent 实测 **$6.150604**（off_peak 100%）+ v4 验证 $0.056447（授权 $0.20 内）已在内；sim 侧待用户余额核对。
- **外部状态收尾**：compose.bird 三服务与官方库 5433 已 stop（回到开场前）；产品 PG 未动；无残留进程。

### 8.12 消融轮（2026-09-17，付费门行使 + 文档，commit 待授权）

- **已入库（授权执行下一步时）**：`43e840a`（并发 cap）、`b52a568`（a 批文档）、`e0b3844`（条件 A）、`d70b429`（compose 接线）、`e87391b`（消融选取工件）。
- **待 commit（消融收官）**：`docs/reports/2026-09-17-ablation-repair-120.md`、执行日志 §33、本文件、`CLAUDE.md`。
- **付费**：agent **$1.673449 ≈ 11.8 元**（cap 25 元内；B $1.1885 午窗 + A $0.4850 晚窗，off_peak 100%）；sim 待余额核对。
- **DB 现场变更（消融授权内）**：eval 库新增 2 实验身份 120 attempt 行 + telemetry（坑 79 预聚合修正后 120/120 精确归属）；migration head 仍 0007。
- **gitignored 产物**：events ×4、episodes 120、spool +276、账本 `ablation_reestimate_20260917` + `ablation_repair_20260917` 节、`import_ablation_telemetry.py`（含坑 79 预聚合修正）。
- **外部状态收尾**：栈与官方库已 stop（仅产品 PG）；无残留进程。
