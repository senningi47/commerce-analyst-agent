# CLAUDE.md — CommerceAnalyst 项目接管文档

> 本文件是 **Claude Code 专属**的项目接管文档，2026-09-11 由 Claude Code（Opus 5 1M）依据 Codex 的 34 个会话记录、`HANDOFF.md`、项目规格与现场核验写成。
> Codex 读的是 `AGENTS.md`，不读本文件；两边互不干扰。
> **本文件是参考，不是授权。** 任何 Gate 的授权都必须由用户在新会话中明确给出。

---

## 0. 一句话状态

项目由 Codex 推进 12 天（2026-08-24 → 09-07），Claude Code 于 09-11 接手，09-13 完成 Task 13 Pilot 主运行。

**精确状态（2026-09-14 更新，以 `HANDOFF.md` 为准）：**

```text
Day 4 overall: PASS；Day 5 Phase A + Gate P preflight 全 PASS（模型已切 deepseek-flash）
Task 13 Pilot: 完成（两门 FAIL 为 Day 6 输入基线：预算 457.6 元/160 线（band 经官方定义确认，Pilot 为空闲档计费）、
         能力 rewards 全 0；付费运行纪律：peak 档 = 2×，一律空闲档调度）
Day 6 Phase A: Task 1–5 完成（提交语义排查 / spool 导入接线 / 探针门翻 PASS / v2 decide 三方一致性守卫
         + 旧探针退役 / c-a 策略修复：prompt-policies v3 + c-mode 澄清预算闸）；离线 844 passed / 128 skipped；
         Ruff 全绿；PG 128 passed
下一步: Task 6 Runner SIGINT 演练 → Task 7–9 SSE/UI/E2E
         → Task 10 小样本付费验证（预授权 ≤$0.10，空闲档运行）→ Task 11 Full 重设计对比
已预授权: 逐 Task commit、付费 Gate、PG 写入；不含 push、不含 Day 7 计划、不含 Full 启动
终态证据: 执行日志 docs/reports/2026-09-14-day6-phase-a-execution-log.md；commit 链至 84180e2（未 push）
```

---

## 1. 我接手的是什么

**项目**：BIRD-Interact 电商经营分析与受控运营协同 Agent。用 Olist 2016–2018 匿名历史电商数据，构建一个能在 BIRD-Interact Full 600 题上评测、同时具备「分析 → 提议 → 独立审批 → 受控执行 → 读回」完整闭环的 Agent。

**为什么是我**：Codex 账号额度耗尽。用户希望一路推进到项目结束，之后再由 Codex 复查。目标是**避免重跑、节约时间**。

**我这一侧的资源**：
- 完整源码 70 个 Python 文件（`src/commerce_agent/`，11 个模块）
- `HANDOFF.md`（21 KB，Codex 写的最后交接）
- PowerContext 记忆（scope `git:github.com/senningi47/commerce-analyst-agent`，handoff revision #12，1 条 active memory）
- 从 Codex 34 个会话抽出的 165 条有效用户消息（已清洗，见 §11）

---

## 2. 会话开始时必须先做的事

1. **完整读本文件**，再读 `HANDOFF.md`。把两者都当作**需要现场核验的历史交接**。
2. **先向用户报告准确状态**：

```text
Task 16 / Gate D: PASS
Task 17 / Gate W: PASS
Task 18: NOT RUN
Gate P: NOT REQUIRED / NOT RUN
Gate G: NOT RUN
Day 4 overall: PARTIAL
```

3. **不要假定自己已获授权。** 见 §6 授权协议。
4. 只读取 `HANDOFF.md`、本文件、以及计划 File Map 中明确列出的路径。**不要递归扫描仓库。**
5. 不读 `.env`。不碰 evaluator-only / evaluator_only 内容。
6. 不启动 subagent（除非用户在新会话明确要求 delegation）。
7. 不做任何 Git 写操作。

---

## 3. 项目目标与硬约束

### 3.1 硬约束（全局规格 v0.3 §2.1）

| 项目 | 决策 |
|---|---|
| 产品场景 | 电商经营分析与受控运营协同 |
| 产品数据 | Olist 2016–2018 匿名历史电商数据 |
| 标准评测 | BIRD-Interact Full 600 题 |
| 评测模式 | c/a 各 600，合计 1200 episode |
| 模型 | DeepSeek 官方 `deepseek-v4-flash`；Pro 不进入核心 DoD |
| 预算 | 外部新增费用 ≤ 200 元，目标 100–200 元 |
| 部署 | 本地 Docker Compose，不要求公网 |
| 数据库 | PostgreSQL；BIRD 使用固定官方环境 |
| 硬件 | Ryzen 7 7735HS、约 28GB 内存、AMD 集显、无 CUDA |
| 学习方式 | **用户手敲** Model、Agent、RAG、SQL 安全、审批、评测和关键测试 |
| UI | 先做模拟数据 UI Demo，再实现正式关键路径 |

### 3.2 非目标（不要往这些方向做）

- 不接入真实商家生产账号，不执行真实封禁/调价/补贴/库存操作
- 不声称 Olist 代表 2026 年实时市场
- 不预测「执行操作后下月 GMV」，不从相关性推因果
- 不训练或微调基础模型
- 不做多 Agent 辩论、微服务治理、Kubernetes、流式大数据平台
- 不把订单事实行全部向量化
- 不用 LLM-as-Judge 替代可执行正确性断言
- 不为满足预算私自减少已确认的 1200 个 episode

### 3.3 学习边界（容易被忽略，但用户明确要求）

用户要**亲手实现并解释**：审批/事务/审计、`SqlReasoner`、`ProductScenarioDriver`、BIRD 隔离、关键测试。
→ 每个核心任务评审必须记录：它是什么、为什么存在、失败情形、不变量、依赖、替代方案、可移植性、验证方式。
→ **不要替他写完了事。**

---

## 4. 架构与不可破坏的边界

### 4.1 六个不可破坏的边界

1. **RetailGraph 只能提议。** 它最多持有 proposal-only capability，不得审批或执行。不得把完整 `OperationWorkflow`、approval/execution capability 注入 graph。
2. **`OperationWorkflow` 独占状态机。** 恰好三个公开行为方法：`propose(ProposeRequest) -> ProposalSnapshot`、`decide(DecisionRequest) -> DecisionReceipt`、`execute(ExecuteRequest) -> ExecutionReceipt`。
3. **QueryEngine 独占只读 SQL 与 reconciliation。** `ops_read` 是唯一的分析读回路径。
4. **Product Trace 与 execution audit 是两条独立证据轨道。**
5. **BIRD 与 Product 硬隔离。** Product 的代码、配置、数据、工具、状态、凭据、fixtures 不得进入 BirdA/BirdC 的构造器、profile、工具、fixtures 或运行时状态。
6. **跨实例恢复必须来自 PostgreSQL**，不得用进程内 dict/cache 冒充持久恢复。

### 4.2 模块地图（`src/commerce_agent/`，70 文件）

| 模块 | 文件数 | 职责 |
|---|---|---|
| `operations/` | 12 | proposal/decision/execution 状态机、canonical JSON、HMAC grant、SellerRef、回测、PostgreSQL 适配器 |
| `model/` | 9 | ModelGateway、DeepSeek 协议、provider-private turn 存储、重试、定价 |
| `value_resolver/` | 7 | BusinessValueResolver |
| `orchestration/` | 7 | RetailGraph、BirdAGraph、BirdCResponder、checkpoint、工具 |
| `knowledge/` | 7 | 业务知识 |
| `trace/` | 6 | provider-neutral Trace |
| `query_engine/` | 6 | 只读 SQL、reconciliation、`ops_read` AST allowlist |
| `context_builder/` | 6 | 三轨 Prompt policy、预算、裁剪、canonical hash |
| `sql_reasoning/` | 4 | SqlReasoner 生成/修复循环（不执行 SQL） |
| `product_eval/` | 4 | ProductScenarioDriver、reset 端口 |
| `config.py` | 1 | 可选 typed SecretStr 设置 |

**关键依赖方向**：RetailGraph → OperationWorkflow.propose() （单向）。ModelGateway 不理解 track 语义，不读 bird-coin、阶段、审批或停止原因。

---

## 5. 精确状态（现场核验通过）

### 5.1 已完成

**Tasks 1–12（离线 Product 核心）**
八种强类型 operation command + `propose()`/`decide()`/`execute()` 状态机；canonical JSON；HMAC-SHA256 grant；24h proposal 过期 / 10min grant 过期 / 一次性 nonce；申请审批人分离；first-decision-wins；幂等与 optimistic locking；PostgreSQL 只存 grant 的 canonical SHA-256（不存原始 nonce/signature）；SellerRef；指标确定性回放；SqlReasoner；QueryEngine reconciliation；四个 `ops_read` security-barrier 视图；RetailGraph v2 终态恢复与 provider-private turn cleanup；ProductScenarioDriver；三条确定性闭环 + 负向授权 + 恢复 + 隔离覆盖。

**Task 13 / Gate M** — 编写 `0004_day4_product_operations.py` migration 源（未应用）
**Task 14 / Gate R** — 编写凭据与角色 provisioning 源（未运行）
**Task 15** — 角色绑定的 PostgreSQL 适配器 + 集成契约

**Task 16 / Gate D — 真实 PostgreSQL activation PASS**
创建并加固 Day 4 roles/owners；应用 migration `0004`；受控 `0004 → 0003 → 0004` catalog 重建（在确认 11 张表为空后）；最终 live catalog 核验通过；Gate D 契约 `17 passed in 3.07s`。

**Task 17 / Gate W — 真实 seller-risk transaction/readback PASS**
security/transaction gate `9 passed in 5.59s`；唯一正向 seller-risk scenario `1 passed in 2.77s`。

### 5.2 本轮（Day 4）发现并修复的 5 个缺陷

1. **PL/pgSQL 输出变量歧义** — `RETURNS TABLE` 的 `execution_id` 也是变量，已改为 `UPDATE ops.command_execution AS execution` + `WHERE execution.execution_id = ...`
2. **audit owner 行锁冲突** — `append_audit_event` 原用 `FOR UPDATE`，但 `audit_owner` 只有 SELECT。已移除，**没有扩大权限**。
3. **并发 execute pre-read 竞态** — 后到者在确定性 `execution_id` 主键上得到 `23505`。`PostgresOperationStore.execute_once` 现在仅在 `operation_identity_conflict` 后读同 proposal 的已提交 receipt；读不到仍 fail-closed。
4. **psycopg tuple 不是 PostgreSQL array** — tuple 被适配为复合值 `"(delivered)"`，不能绑定 `text[]`。已在数据库边界显式 `list(...)`。
5. **公开 seller evidence 小数尺度不一致** — PG 整数除法可能超 6 位小数。`SellerTargetResolver` 现在量化到 `Decimal("0.000001")`。

### 5.3 最终数据库现场（2026-09-07 记录）

```text
alembic current            = 0004_day4_product_operations (head)
Day 4 tables empty         = 11/11
Day 4 total rows           = 0
Day 4 application sessions = 0
COMMERCE_AGENT_RUN_POSTGRES_TESTS present = False
LANGGRAPH_STRICT_MSGPACK present         = False
```

11 张受控表：`app.product_trace_event`、`ops.audit_event`、`ops.risk_annotation`、`ops.investigation_conclusion`、`ops.metric_alert_rule`、`ops.command_execution`、`ops.approval_nonce`、`ops.approval_decision`、`ops.operation_proposal`、`ops.alert_backtest_result`、`ops.investigation_task`

### 5.4 现场核验结果（2026-09-11 由我执行）

- `HANDOFF.md` §7 的 **7 个源码/测试 SHA-256：7/7 MATCH**
- 两份规格 SHA-256：**2/2 MATCH**
- Git：`main` unborn、**0 commits、0 tracked files** —— 与 HANDOFF 描述一致
- 结论：**`HANDOFF.md` 内容属实，可以作为可信基线。**

---

## 6. 授权协议（最重要的一节）

### 6.1 每个 Gate 独立授权，跨会话不继承

| Gate | 交付物 | 需要单独授权 |
|---|---|---|
| Plan Gate | 计划文档本身 | — |
| Implementation Gate | Tasks 1–12 离线源码/测试 | 是 |
| Migration Source Gate (M) | Task 13 创建 migration 源 | 是 |
| Role Provisioning Source Gate (R) | Task 14 凭据/provisioning 源 | 是 |
| Database Activation Gate (D) | Task 16 创建角色 + 应用 migration | 是 |
| Real PostgreSQL Write Gate (W) | Task 17 真实 seller-risk 写入 | 是 |
| Paid Provider Gate (P) | 可选，Day 4 不需要 | 是 |
| Git Checkpoint Gate (G) | 暂存并创建 commit | 是 |

**批准上一行永不授权下一行。** 写 migration 源不等于授权执行它。Database Activation 不授权 Product 写入。

### 6.2 关于 Task 18 的授权状态（重要细节）

Codex 最后一个会话（`09/07 19:44`）留下一条消息：**「阅读HANDOFF.md，授权执行Task18」**。该会话只产生 2 条消息、1 KB，**没有任何实际工作**。

按 `HANDOFF.md` §0.3 与 §6.1 的规则：**跨会话授权不继承**。因此：

> **我在新会话中不持有 Task 18 授权。需要用户重新明确授权。**

同理，Task 18 的授权**不包含** Gate P（付费请求）和 Gate G（`git add`/commit/push/clean/reset/checkout）。

### 6.3 用户的工作习惯（从他的 165 条消息提炼）

- **会话开头**：几乎固定是「阅读HANDOFF.md，开始TaskN」或「阅读HANDOFF.md，开始执行」
- **会话结尾**：几乎固定是「这个会话要结束了。请写一份交接文档存到 HANDOFF.md：我们在做什么任务、已经完成了什么、当前卡在哪、下一步计划是什么、有哪些踩过的坑绝对不要再踩。写给一个完全没有上下文的新会话看。」——**这是他的核心流程，最后几天几乎每个会话都这样收尾**
- **授权用语**：「授权执行 X」「确认授权」「授权继续」「批准」「评审通过，开始执行」「确认并授权执行」
- **他会反复纠正的**：把「开新会话」误解成「创建 git worktree」。他说过至少 3 次：「不要working tree，是创建新会话」「是开一个新会话，不是分支工作树」「撤销该工作树」
- **他要求 PowerContext 交接**：「交接当前工作。请提交最新的 PowerContext Handoff，并返回 exact revision。不要修改 HANDOFF.md，不要提交 Git。」

---

## 7. 下一步：Task 18 六步

**入口条件**：Tasks 1–15 完成；Gate D/W 结果可为 PASS 或显式 NOT RUN，但报告必须保留精确状态，**不得把缺失证据升级为 PASS**。

**唯一新建文件**：`docs/reports/2026-09-06-day-4-product-closed-loops.md`
**唯一允许的修改**：若失败测试暴露出已批准的 Day 4 缺陷，修改 Tasks 1–15 对应源文件/测试，并走本地红绿重构。

### Step 1 — Ruff + 完整默认离线 suite

```powershell
uv run ruff check src tests scripts db/migrations
uv run pytest -q --tb=line
```

预期 PASS；PostgreSQL 与 DeepSeek marker 默认 skip；三条确定性闭环用 FakeModel/in-memory 通过；无网络、无数据库变更。
默认 suite **不得**设置 PostgreSQL 或 DeepSeek 开关。

### Step 2 — 完整 PostgreSQL integration suite（需 Gate D 已激活且获授权）

```powershell
$env:COMMERCE_AGENT_RUN_POSTGRES_TESTS = '1'
$env:LANGGRAPH_STRICT_MSGPACK = 'true'
try {
    uv run --env-file .env pytest tests/integration -m postgres -q --tb=line
}
finally {
    Remove-Item Env:COMMERCE_AGENT_RUN_POSTGRES_TESTS -ErrorAction SilentlyContinue
    Remove-Item Env:LANGGRAPH_STRICT_MSGPACK -ErrorAction SilentlyContinue
}
```

失败时**停止**，不跑长 traceback。先确认 11 张表、application sessions 和开关清理状态，再申请诊断/修复授权。

### Step 3 — 显式 BIRD-isolation gate（离线）

```powershell
uv run pytest `
  tests/unit/orchestration/test_track_isolation.py `
  tests/unit/orchestration/test_bird_a_graph.py `
  tests/unit/orchestration/test_bird_c_responder.py `
  tests/unit/orchestration/test_bird_tools.py `
  -q --tb=line
```

只跑离线契约；不得连接、枚举或读取 BIRD service/evaluation data。

### Step 4 — 只扫描 Day 4 显式 allowlist

从计划 File Map + 两份命名 Day 4 fixture + 待生成 report 构建精确文件数组。**必须排除**：仓库根目录泛扫描、`.env` 与其他 secret 文件、`data/`、outputs、cache、evaluator-only / evaluator_only 路径、BIRD service/evaluation data。

敏感模式要在**运行时拼接**，避免 guard 文本自匹配。检查项：

```text
no credential-like value or connection string
no raw seller identity value
no nonce/signature/HMAC key value
no complete provider request/response or private reasoning payload
no arbitrary write SQL, generic command batch, patch, handler, URL, or DSN field in public contracts
no final-closed reference answer in development/regression fixtures
all evaluator-only terms occur only in access-denial guard assertions/docs
```

### Step 5 — 只依据实际命令证据写报告

报告须记录：规格/设计路径与 SHA-256；实现/config revisions 与 hashes；Ruff/默认 suite/PostgreSQL/BIRD-isolation 的精确命令、数量、时长、状态；三个确定性 scenario ID 与断言摘要；negative authorization matrix（成功业务写入 = 0）；proposal/decision/execution/nonce/business/audit 状态转移；SellerRef 隐私证据与敏感度比较；backtest 的 historical-only hit/exclusion/coverage；SqlReasoner 生成/修复/no-progress 与 QueryEngine reconciliation；role/object/function/view ACL 矩阵与安全函数属性；response-loss 恢复、幂等与零 session；Gate M/R/D/W/P/G 精确状态；遗漏、限制与 overall decision。

**Day 4 overall 只有在 Gate W 真实 PostgreSQL seller-risk scenario 通过，且 offline/integration/ACL/privacy/BIRD-isolation 全部通过时才可标 PASS。** 否则写 `PARTIAL / REAL POSTGRESQL GATE NOT RUN`。确定性 fixture 不能冒充最终 PostgreSQL 证明。另两条闭环仍标为 deterministic Product evidence。

### Step 6 — 核对报告，停在 Gate P/G

每个数字和结论都必须能回指本轮精确命令或文件。完成后停止并向用户汇报。**不调用付费 provider，不执行 Git staging/commit/push。**

**完成标准**：所有已授权非付费检查有精确结果；报告区分 fixture / PostgreSQL / not-run 三类证据；负向写入保持为 0；限制显式列出；不从报告中推断出任何付费或 Git 动作。

---

## 8. 绝对不要再踩的坑

### 8.1 授权、秘密与外部状态

1. 每个 Gate 独立授权；Gate D/W PASS 不批准 Task 18/P/G；新会话不继承旧的数据库授权
2. **默认 skip 不是数据库证据** —— 只有显式设置 PostgreSQL 开关并运行目标测试才算真实数据库验证
3. 根 `.env` **不可人工查看**；只允许已审核脚本或 `uv run --env-file .env` 消费；stdout 只保留 sanitized 状态
4. 诊断只输出最小异常元数据（异常类型、reason_code、SQLSTATE、sanitized primary message）。**禁止 `pytest -vv --tb=long`**；禁止输出 query params、payload、DSN、密码、HMAC key、grant、nonce、signature、private seller ID、server detail
5. **一次正向运行就是一次授权额度。** 失败后要诊断重跑，先重新取得授权，不要把「诊断」当免费重试
6. 所有真实场景必须 `finally` reset；无论成功失败都核验 11 张表、application sessions 和两个测试开关

### 8.2 Migration、PL/pgSQL 与 ACL

7. migration 失败立即停止，不自动重试、不手工补 grant、不静默修 catalog
8. 已应用 migration 的源码改动会造成 catalog 漂移；只有在明确授权 + 精确 revision 核验 + 11 张表为空后才可受控 downgrade/reapply
9. `RETURNS TABLE` 输出名也是 PL/pgSQL 变量；所有同名表列必须用 table alias 限定（尤其 `execution_id`、`proposal_id`、`proposal_version`）
10. 最小 SELECT 权限不含行锁；`audit_owner` 只有 SELECT 时不得用 `FOR UPDATE`。**不要用扩大权限掩盖函数设计错误**
11. 保留字必须处处一致引用（`metric_alert_rule.window` 曾在 CHECK/view/INSERT 漏引号导致真实 migration 失败）
12. JSON 运算符要显式括号：写 `text || (json->>'field')`
13. 表列权限 ≠ 对象可访问；角色还需要 schema USAGE
14. DELETE predicate 需要 SELECT；reset owner 的最小权限是 `SELECT(scenario_id)` + DELETE
15. deny-path ACL test 由 admin 解析对象；受限角色可能没有 schema USAGE，别让它做 privilege introspection
16. `SECURITY DEFINER` 必须同时满足：固定安全 search_path（`trusted_schema, pg_temp`）、schema-qualified SQL、PUBLIC revoke、精确 owner、最小 grant
17. login role 与 function owner 不可混淆（`product_scenario_reset` 是登录角色，`scenario_reset_owner` 是 NOLOGIN owner）

### 8.3 Adapter、并发与数据契约

18. workflow pre-read 不能消除并发竞态。deterministic identity 的后到请求收到 `23505` 时，必须在事务回滚后读同 proposal 的成功 receipt；只有读到 receipt 才能恢复，其他 identity conflict 保持 fail-closed
19. commit response loss 是 **outcome unknown** —— 先 readback 再决定是否重试，禁止盲目重复业务写入
20. psycopg 的 tuple 是复合值不是数组；PostgreSQL `text[]` 参数要绑 Python list
21. 公共 Decimal 契约要在边界固定尺度（公开 `normalized_value` 必须与 backtest 一致量化到 6 位）
22. 跨实例恢复走 PostgreSQL 窄化 read path，不得退回实例内 dict/cache
23. private identity 只在受控边界内存在；`ops_read.risk_annotations`、Trace、audit、checkpoint、report 和 public payload 只能出现 opaque SellerRef
24. Product Trace、execution audit、BIRD trace 不能混用

### 8.4 编排、隔离与验证

25. RetailGraph 只能持有 proposal-only port；不得注入完整 `OperationWorkflow` 或 approval/execution capability
26. terminal state 先持久化，再清理 provider-private turns；cleanup retry 只能 finalize，不能重复前序副作用
27. failure injection 比较精确 idempotency key，不比较 `repr`
28. `query:<step_id>` evidence ID 两端必须兼容，不要单边改生成或解析约定
29. SQL repair 三次预算属于**整个 attempt**，不是每个 plan step 各三次
30. **BIRD 隔离是硬门**
31. **只扫描显式 allowlist** —— 仓库级递归扫描可能触及 evaluator-only 或 secret 内容
32. 测试数字必须带范围和时间边界。历史 `595 passed` **不代表**最终源码；Task 18 必须产生新证据
33. 失败后先执行 owning test，再执行捕获缺陷的 broader gate；不要只跑新单测就宣称修复完成
34. 仓库没有 Git 基线（unborn main、0 commits、文件未跟踪）；**空 `git diff` 没有意义**
35. 不要启动 subagent（除非用户明确要求）

---

## 9. 关键文件与哈希

哈希用于发现意外变化，**不代表文件已 commit**。2026-09-11 现场核验：全部 MATCH。

| 文件 | SHA-256 |
|---|---|
| `db/migrations/versions/0004_day4_product_operations.py` | `b1fba3d574c0251848b6778f4a7527a5fda32fbfc44e8a585877cb351967e498` |
| `src/commerce_agent/operations/_postgres.py` | `437bd1efba4f0894f9b9230e84b16da82671682548515100e45b3ea1c3d77782` |
| `src/commerce_agent/operations/_seller_refs.py` | `1060607eba93c11f856eaf235a60eda2e797867f49e3583d55f216f16499e86b` |
| `tests/unit/test_day4_operations_migration.py` | `80b4566ae8fa1b6cb566c72a10bc632c2dbc1749479e188291e8c4e5756df822` |
| `tests/unit/operations/test_postgres_adapter.py` | `4843394b4bd10501214a7ab0fa8c59240b7dd95af891882c985d837d60377208` |
| `tests/unit/operations/test_seller_refs.py` | `f58b4e53ba75b98f19116d8333dbb300f2717396f85be7e282db220b1a62b731` |
| `tests/integration/product_eval/test_seller_risk_postgres_scenario.py` | `d67589266c951d3ce23d97f0a6bdfa56b49e1f5db2b5b2185ebe5292e6de9705` |
| `docs/project/specs/BIRD-Interact电商经营分析Agent-设计规格-v0.3.md` | `33466c117bc35ac036c334bf2b120bba4071605545d19e09d4f086df78290c81` |
| `docs/project/specs/2026-09-06-day-4-product-closed-loops-design.md` | `d76d260e1dddd070f4f1d89d4d6145db6e9457b71a6a271fe13c5a1cbf842386` |

**不要覆盖或回退这些文件。** 若现场哈希不同，先确认是否是用户或其他会话的新修改。

---

## 10. 文档地图

### 权威规格（优先级从高到低）

1. `docs/project/specs/BIRD-Interact电商经营分析Agent-设计规格-v0.3.md` — **全局权威规格**。与任何 Day 文档冲突时以它为准；冲突要停下提交用户审阅
2. `docs/project/specs/2026-09-06-day-4-product-closed-loops-design.md` — Day 4 已批准设计
3. `docs/superpowers/plans/2026-09-06-day-4-product-closed-loops.md` — Day 4 实施计划（159 KB，18 个 Task）

### 按日期的设计/计划/报告

| Day | 设计 | 计划 | 报告 |
|---|---|---|---|
| 仓库初始化 | — | `docs/project/planning/2026-08-24-commerce-analyst-repository-initialization-plan.md` | — |
| Day 1B 骨架 | — | `docs/project/planning/2026-08-29-day-1b-minimal-skeleton-implementation-plan.md` | — |
| Day 2A 查询引擎 | `docs/project/specs/2026-08-30-day-2a-query-engine-design.md` | `docs/superpowers/plans/2026-08-30-day-2a-product-query-engine.md` | `docs/reports/2026-08-30-day-2a-query-engine.md` |
| Day 2B 业务值知识 | `docs/project/specs/2026-08-31-day-2b-business-value-knowledge-design.md` | `docs/superpowers/plans/2026-08-31-day-2b-business-value-knowledge.md` | `docs/reports/2026-09-01-day-2b-business-value-knowledge.md` |
| Day 3 模型上下文编排 | `docs/project/specs/2026-09-01-day-3-model-context-orchestration-design.md` | `docs/superpowers/plans/2026-09-01-day-3-model-context-orchestration.md` | `docs/reports/2026-09-01-day-3-model-context-orchestration.md` |
| Day 4 产品闭环 | `docs/project/specs/2026-09-06-day-4-product-closed-loops-design.md` | `docs/superpowers/plans/2026-09-06-day-4-product-closed-loops.md` | **待创建**：`docs/reports/2026-09-06-day-4-product-closed-loops.md` |

### 调研与评审

- `docs/project/research/2026-08-30-bird-gt-fallbacks.md`
- `docs/project/research/2026-09-01-deepseek-model-gateway-contract.md`
- `docs/project/research/v0.3-一手资料核验.md`
- `docs/project/reviews/v0.3-自审报告.md`
- `docs/reports/2026-08-29-deepseek-capability-probe.md`
- `docs/reports/2026-08-30-bird-full-database-import.md`
- `docs/reports/2026-08-30-bird-stub-concurrency-spike.md`
- `DATA_PROVENANCE.md`（数据来源与许可）

### 进度文件（Codex 早期用的）

- `docs/project/planning/task_plan.md`、`findings.md`、`progress.md`

---

## 11. 历史轨迹（12 天 / 34 会话）

| 日期 | 会话数 | 关键进展 |
|---|---|---|
| 08-24 | 1 | 仓库初始化 |
| 08-29 | 4 | Day 1B 骨架；用户拿到 BIRD 官方 GT 附件；Stub Spike |
| 08-30 | 3 | **Day 2A 批准（方案 A + 书面设计）**；PostgreSQL 向导；六段式 spike |
| 08-31 | 1 | Day 2A 实施；用户手工输入数据库密码 |
| 09-01 | 7 | **Day 3 设计**：4 个并行 subagent 各自给出 ModelGateway/ContextBuilder 接口方案（09/01 04:40 同时返回）；用户「确认，并开一个新会话，在新会话开始实施计划」 |
| 09-04 | 1 | Day 3 实施续；用户「撤销该工作树」 |
| 09-05 | 3 | Task 6 收尾；**用户尝试让另一个 Codex 账号接手，PowerContext 找不到记忆 —— 未解决** |
| 09-06 | 8 | Task 7–12 连续推进；**Day 4 设计评审通过**；「后续架构设计要以全局规格为标准去判断」 |
| 09-07 | 6 | **Task 13 / Gate M → Task 14 / Gate R → Task 15 → Task 16 / Gate D PASS → Task 17 / Gate W PASS**；写最终 HANDOFF.md；最后一个会话授权 Task 18 但无产出 |

### 11.1 一个未解决的历史问题（值得你知道）

2026-09-05 20:33 的会话里，用户问 Codex：

> 「这些项目记忆不应该是共享的吗？为什么你powercontext没有找到记忆？而且交接文件还没更新，想想怎么让你继承共享旧会话的记忆，旧账号我还可以用的」

**他 9 月 5 日就尝试过换账号接手，失败了** —— PowerContext 在另一个账号下找不到记忆。

**今天（2026-09-11）我验证了这个问题的根因**：PowerContext 的 scope 是 **Git 派生**的 `git:github.com/senningi47/commerce-analyst-agent`，与账号无关。记忆其实**一直在**（1 条 active memory + 73 条待处理 source + handoff revision #12）。当时找不到，很可能是因为**那个 Codex 会话没有装/启用 PowerContext 集成**，而不是数据不共享。

→ 现在 Claude Code 侧已按方案 A 接好（见 §12），**这个问题在当前配置下不会重现**。

### 11.2 原始会话数据在哪

- Codex 原始会话：`C:\Users\Lenovo\.codex\sessions\2026\**\rollout-*.jsonl`（34 个涉及本项目，原始 118 MB）
- **已清洗的用户消息摘要**（165 条 / 63 KB，去掉了 `recommended_plugins`、`environment_context`、AGENTS.md 注入等噪声）：
  `C:\Users\Lenovo\.claude\projects\D--git-projects-commerce-analyst-agent\takeover\codex-user-messages.md`
- 抽取脚本思路见本文件 §11 描述；如需重跑，按 `commerce-analyst` 关键词过滤 `rollout-*.jsonl`，取 `payload.type == "message" and payload.role == "user"`

---

## 12. 我这一侧的工具环境（2026-09-11 由我配置）

### 12.1 Skills

- 源：`C:\Users\Lenovo\.agents\skills\`（**Codex 的那份，未被改动**）
- 我的副本：`C:\Users\Lenovo\.claude\skills\`（复制而非软链，47 个）
- 已删除 1 个：`code-review`（与 Claude Code 内置同名且不含项目上下文）
- 未复制 2 个：`codegraph-main`、`powercontext-master`（是仓库克隆，不是 skill）
- 14 个原本 `disable-model-invocation: true` 的 skill，已在**我的副本**上摘除该字段，现在可自主调用：`ask-matt`、`grill-me`、`grill-with-docs`、`handoff`、`implement`、`improve-codebase-architecture`、`setup-matt-pocock-skills`、`teach`、`to-questionnaire`、`to-spec`、`to-tickets`、`triage`、`wait-what`、`wayfinder`

对本项目有用的：`tdd`、`systematic-debugging`、`writing-plans`、`codebase-design`、`domain-modeling`、`diagnosing-bugs`、`prototype`、`research`、`full-output-enforcement`、`handoff`

### 12.2 已装插件

| 插件 | 状态 |
|---|---|
| `claude-code-setup@claude-plugins-official` | enabled（用户要求保留） |
| `powercontext@powercontext` | enabled（用户选择方案 A） |

已卸载并清理：`superpowers`、`ecc`、`claude-mem`、`planning-with-files`、`frontend-design`、`andrej-karpathy-skills`（plugins 目录 747 MB → 13 MB）

### 12.3 PowerContext（方案 A 已接通）

- **Server**：`http://127.0.0.1:8000`，2026-09-11 起**以脱离会话的独立进程运行**（`Start-Process powercontext server run`，日志在 `%LOCALAPPDATA%\powercontext\server.log`），不随 Claude Code 会话退出。停止：`Stop-Process -Name powercontext`；重启：`Start-Process "$env:USERPROFILE\.local\bin\powercontext.exe" -ArgumentList 'server','run' -WindowStyle Hidden`
- **数据**：`C:\Users\Lenovo\AppData\Local\powercontext\powercontext.db`
- **scope**：`git:github.com/senningi47/commerce-analyst-agent` —— **与 Codex 共用同一份**
- **MCP**：23 个工具可用（`remember_memory`、`search_memory`、`list_memory_entries`、`handoff_current_work`、`continue_handoff`、`approve_artifact_candidate` 等）
- **hook**：`UserPromptSubmit` 自动 recall 并注入 PreparedContext；同时把当前 prompt 作为 Source 捕获
- **`/project-context` skill**：说「交接」即可创建 handoff

**我为 Windows 做的两处兼容修复**（改的是 `C:\Users\Lenovo\.claude\pc-marketplace\` 里的中性副本，**没碰 Codex 的 checkout**）：
1. `headersHelper` 原本是 `python3 -c '...'` 内联单引号 —— 在 cmd 下报 `SyntaxError`。已改为调用插件自带的 `scripts/mcp_headers.py`
2. MCP URL 从 `/mcp` 改为 `/mcp/` —— 原地址返回 307 重定向，加尾斜杠直连 200

### 12.4 CodeGraph（用户选择方案 B，但**本项目不可用**）

- CLI 已装：`codegraph`（v1.5.0，`@colbymchenry/codegraph`）
- **本项目没有 `.codegraph/` 索引**
- **结论：不可用。** `codegraph init` 没有任何 exclude/ignore 选项，会递归索引整个仓库 —— 这直接违反 `HANDOFF.md` §0.4 / §5 Step 4 / §6.4 第 31 条（只允许显式 allowlist 扫描，禁止触及 evaluator-only 与 secret 内容）
- **未建索引，未做任何配置变更。** 若要用，需要你先解决「如何在不扫描 evaluator-only 内容的前提下建索引」

### 12.5 环境注意

- `python3` 可用（Python 3.11.9，Microsoft Store 版）
- `~/.codex/config.toml` 里 `experimental_bearer_token` 是**明文**存的 —— 建议挪到环境变量

---

## 13. 我的工作方式约束

**语言**：与用户用中文交流。代码、提交信息、标识符按项目既有约定。

**TDD 是强制的**（计划 Global Constraints 第 39 条）：每个垂直切片先加一个具名测试 → 跑该节点观察指定失败 → 加最小实现 → 重跑通过 → 在绿色下重构 → 继续。**宽泛 suite 是回归检查，不能替代红/绿/重构。**

**不要做的事**：
- 不 `git add` / `commit` / `push` / `clean` / `reset`（Gate G 未授权）
- 不连接 PostgreSQL（除 Task 18 Step 2 获授权时）
- 不调用付费 provider
- 不启动 subagent（除非明确要求）
- 不递归扫描仓库
- 不读 `.env`
- 不替用户把他该手敲的核心代码写完

**报告纪律**：每个数字和结论都必须能回指精确命令或文件。**不要把部分成功升级为整体 PASS。**

---

## 14. 快速上手清单

```text
[ ] 读本文件
[ ] 读 HANDOFF.md
[ ] 向用户报告精确状态（§2.2 的六行）
[ ] 确认 PowerContext server 在跑（powercontext doctor）
[ ] 询问用户本次要做什么 / 是否授权 Task 18
[ ] 若获授权：按 §7 六步执行
[ ] 若不是授权而是别的任务：先只读核验，再确认范围
[ ] 不碰 Git、不碰 .env、不碰 evaluator-only
```
