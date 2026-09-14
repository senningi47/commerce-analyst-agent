# CommerceAnalyst 项目交接：Task 13 Pilot 主运行完成（18/20 有效终态，agent 侧 1.49 元），待 commit 授权与 Pilot 报告期

> 更新时间：2026-09-13（Asia/Shanghai），更新者：Claude Code（Opus 5 1M）  
> 工作区：`D:\git-projects\commerce-analyst-agent`  
> 当前分支状态：`main` @ `9f4df34`（23 commits，未 push；Task 13 Pilot 完成，六缺陷修复与收尾文档已按用户裁定分两个主题 commit 入库）  
> 交接状态：`continuable`  
> PowerContext scope：`git:github.com/senningi47/commerce-analyst-agent`  
> Durable Handoff：PowerContext `handoff/handoff#19`（2026-09-13 提交，exact revision=19）

## 0. 新会话先做什么

1. 完整阅读本文件、`docs/reports/2026-09-14-day6-phase-a-execution-log.md`（Day 6 Phase A 前段执行日志）与 `docs/superpowers/plans/2026-09-14-day6-capability-restore-runner-and-ui.md`（Day 6 计划）。把它们当作需要现场核验的历史交接，不要把历史授权当作新会话授权。
2. 先向用户报告准确状态：**Day 6 Phase A（Task 1–4）已完成并 commit**——Task 1 提交语义排查（根因 = prompt 缺官方策略，通道无缺陷）、Task 2 spool 导入接线（回填能力就绪，d 旧格式不可回填）、Task 3 探针重设计（**探针门翻转为 PASS**，付费 $0.000035334×2 次调用）、Task 4 v2 decide 三方一致性守卫（图 allowlist 对齐 v2 规则 + 旧探针退役）。**下一步 = Task 5（c/a 策略修复）→ Task 6（Runner SIGINT 演练）→ Task 7–9（SSE/UI/E2E）→ Task 10（小样本付费验证，上会话已预授权 ≤$0.10）→ Task 11（Full 重设计对比）**。用户已预授权：逐 Task commit、付费 Gate、PG 写入；决策按推荐执行、日志记录即可。
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

**Band 修正（2026-09-14 续会话，用户输入，详见 Pilot 报告 §8 与执行日志 §8）**：用户澄清 **6.43 元为高峰时段计费**、此前价格表为空闲时段档，并提供完整 CNY 价格表（off-peak ¥0.02/¥1/¥4 每百万、peak 恰 2×）→ §3.5 的 band 排查结论被推翻。按平台 CNY 列/快照 USD 列 = 6.6667 重拆：**agent 实际 3.74 元 / sim 实际 2.69 元（比例 0.72×，原 2.24× 反转）**；sim 单价修正 $0.01008/集（off-peak 基准，取代计划约束 #9 的 $0.03144）。外推两档**均 FAIL**：peak-run **461.4 元**（2.88×）/ off-peak-run **233.9 元**（1.46×）——**off-peak 调度成为 Task 11 首要降本杠杆**；「a-mode 单项超线」仅 peak 档成立（off-peak 档 113.6 元）。**band 模型缺陷待修**（`_pricing.py` weekday 门 + 快照 evidence 与平台不符；需用户提供真实峰值窗口后重冻结 v5）；**Task 10 ≤$0.10 在 peak 档下 4 集预估 ~$0.20 越限、off-peak 档贴线 ~$0.098——执行前须定档或重确认上限**。账本与报告已更新（历史数字保留作审计痕迹）。

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
5. **Band 修正（用户输入，零付费）**：用户澄清 Pilot 6.43 元为高峰计费并给出完整 CNY 价格表 → 对账重拆（agent 3.74 / sim 2.69，比例 0.72×）+ 两档外推均 FAIL（peak 461.4 / off-peak 233.9 元）+ band 模型缺陷记录（待用户供给峰值窗口后修 `_pricing.py` + 重冻结快照 v5）+ Task 10 上限重估提醒。账本（gitignored）、Pilot 报告 §8、HANDOFF §2.10、执行日志 §8 已同步。

## 3. 当前卡在哪里

**没有技术阻塞。** Day 6 Phase A（Task 1–4）完成，新会话从 Task 5 继续：

- **用户已预授权（2026-09-14 深夜）**：① 剩余决策按执行者推荐行使；② 付费 Gate（Task 3 探针已用毕、Task 10 小样本 ≤$0.10 含 sim 侧）与 PG 写入；③ 逐 Task commit 打包授权；④ 只要求日志记录与上下文收尾。以上授权覆盖 Day 6 计划范围，**不含 push、不含 Day 7 计划、不含 Full 启动**（Task 11 仍只产出方案对比）。
- **下一步顺序**：Task 5（c/a 策略修复：prompt-policies v3 + 澄清预算闸；Task 1 结论已给形状）→ Task 6（Runner SIGINT 演练，双开关）→ Task 7→8→9（SSE/UI/E2E，`api/`、`web/`、`tests/e2e/` 全新）→ Task 10（小样本验证，新 experiment，须在 Task 5 红绿后）→ Task 11（Full 重设计方案对比，交用户裁定）。
- 关键输入：Task 1 研究笔记（`docs/project/research/2026-09-14-submit-semantics-alignment.md`）已定 Task 5 修复形状；PG 测试需双开关 `COMMERCE_AGENT_RUN_POSTGRES_TESTS=1` + `LANGGRAPH_STRICT_MSGPACK=true`。
- `cybermarket_pattern_12 [a]` unfinished 恢复（可选，需新会话向用户确认）；`crypto_exchange_9 [c]` failed 有效不重跑。
- Day 7（产品 50 题、实验、Full 进度/收尾、README/面试材料）需独立计划；Full 启动与否在 Task 11 后由用户裁定。

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

1. **Task 4–11 按 Day 6 计划顺序执行**（新会话）：Task 4 v2 decide 守卫 → Task 5 策略修复 → Task 6 Runner 演练 → Task 7/8/9 SSE/UI/E2E → Task 10 小样本（付费已预授权，≤$0.10）→ Task 11 Full 重设计对比。
2. **每 Task 纪律**：TDD 红绿 → owning tests → 全量 suite + Ruff → commit（打包授权范围内）。
3. **收尾三件套**（长期规则）：① 更新本文件；② PowerContext handoff 并返回 exact revision；③ `docs/reports/` 执行日志。
4. **可选（需向用户确认）**：`cybermarket_pattern_12 [a]` 按 §8.5.3 同 experiment（d）恢复。
5. **Day 7 计划**（独立计划 Gate）：产品 50 题、实验、Full 进度/收尾、README/面试材料——Full 启动与否在 Task 11 方案对比后由用户裁定。
6. **收尾三件套**（长期规则）：① 更新本文件；② PowerContext handoff 并返回 exact revision；③ `docs/reports/` 执行日志。

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
