# CommerceAnalyst 收官只读审查

审查日期：2026-09-18。基线 `d87768a51c5e41b864133a2235d42c9e34ede048`，现场 HEAD `e73cf3a`；`git diff d87768a...HEAD`，增量 80 commits，整个历史 81 commits。本报告是唯一新增交付文件，未暂存。未修改源码、原有文档、题集或账本；未调用模型 API、启动 BIRD 栈、访问 BIRD GT/evaluator-only 内容、读取 `.env` 本体或执行数据库写操作。

## 1. 总评

**不建议原样采信收官报告；工程增量有实质价值，但数字收口与评测有效性尚未通过审查。** 907/140 和 Ruff 可以复现，消融 120 集、P1 零增益及主要 agent 费用也有数据库与原始工件支持；但 c 批成功数被重复计算，reward 分母混用，产品评分存在已影响结果的数值类型错误，预算估算被写成平台实扣终账。另复现了 SSE 重放、Runner 停止/取消、异常传播及 SQL 身份投影边界缺陷。能力门未达的方向成立，检索未达预注册保留门槛的裁决仍成立；“正确率 7.5%/5.0%”“所有完成数字可追溯”“工程交付完整”等结论须收窄或更正。下文将新发现与已披露限制分开，并标明基线遗留，避免把旧缺陷归罪于新增提交。

### 审查方法与限制

- 按指定入口复核 HANDOFF、最终报告、README、面试材料、执行日志、账本及 Day 7 计划；以工件/当前源码为证据，不把报告互相引用当独立验证。
- 使用 code-review 的标准/规格两个视角，但遵守项目禁止 subagent 的约束，均由主会话完成；新缺陷按 systematic-debugging 做根因追踪与最小复现，未实施修复。
- `.codegraph/` 不存在，未使用 CodeGraph；只读取明确相关路径。`rg` 被 WindowsApps ACL 阻止后用 PowerShell 定向查询。
- `uv` 缓存与 Docker 管道被沙箱拒绝后，经执行权限流程运行相同的验证/只读查询。
- 默认测试没有加载 `.env`，140 skipped 不能充当本轮 live PG PASS。本轮没有重跑会建立/清理 DB fixture 的 PG 集成测试，也没有重做容器 GT 拒绝检查；这些历史声明保留“本轮未独立验证”。
- 题集 builder 原入口会覆盖 `data/product-eval/*.jsonl`，故以 `runpy` 调用其原 `main()`，仅把 `OUT_DIR` 重定向至自动清理的临时目录，并用 `PGOPTIONS=-c default_transaction_read_only=on` 强制只读。无题集覆盖。

## 2. 声明抽验表

PASS 表示在表内限定口径下已验证；FAIL 表示原声明不成立或证据不足以支持其完整措辞，后者不等同于已经证明反面。

| # | 声明及来源 | 结论 | 本轮证据 |
|---|---|---|---|
| 1 | 最终报告 §5：907 passed / 140 skipped | PASS | `uv run pytest -q --tb=line`：**907 passed, 140 skipped, 2 warnings in 20.83s**，exit 0。 |
| 2 | 最终报告 §5：Ruff 全绿 | PASS | `uv run ruff check src tests scripts db/migrations`：`All checks passed!`，exit 0。范围按用户指定命令，不扩大到未列出的目录。 |
| 3 | 最终报告 §3.1：a 批 223 成功、2 失败、75 未跑 | PASS | `a4-a-20260916-*`：225 attempts、223 succeeded、2 failed、225 不同 task/mode；相对 300 清单余 75。含 3 个 v4check，须披露混合策略。 |
| 4 | 日志 §31、账本：c 批 304/304 succeeded | FAIL | `a4-c-*` 实际 **305 attempts = 300 succeeded + 4 failed + 1 infrastructure_error**，300 不同 task/mode。300 成功已包含 sweep 4 次，不能再加 4。 |
| 5 | 最终报告 §3.1：c 批 300/300 任务完成 | PASS | 300 个不同 c task 最终各有成功 attempt；这是任务覆盖口径，不是原始 attempts 全成功。 |
| 6 | README/面试：527/529 succeeded、reward 0/627 | FAIL | A4 共 **530 attempts、523 succeeded、6 failed、1 infra**，非空 reward 523 条。加消融后 **650 attempts、643 个非空 reward、正 reward 0**。627 无法由这些指定实验重建。全库另为 748 attempts、669 非空 reward、0 正值。 |
| 7 | 最终报告 §3.1：reward 0/525、含消融 0/645 | FAIL（口径未说明） | 525 是 A4 不同 task/mode 尝试数，含 2 个 a 失败且 reward 为 NULL；645 是加 120 消融 episodes 的混合去重口径。可写“525 个尝试任务中无正 reward，其中 2 个无评分”；不能把 NULL 默认为实测 reward=0。 |
| 8 | 最终报告 §3.2：消融 120/120 succeeded | PASS | `ablation-repair-20260917-a/b` 各 c/a 30，全部 succeeded；结果 120 条。 |
| 9 | 最终报告 §3.2：P1 A 0/60、B 0/60，救回 0 集 | PASS | DB 四格 P1 均 0；同题 `archeology_scan_10` 的 A/c、B/c episode JSON 又分别复算 1/2 次 submit，`passed` 全 false、reward 和为 0，与 DB 一致。仅支持该样本的观察结果。 |
| 10 | 消融报告：agent $1.673449，归属已修正 | PASS | DB A/c $0.107509902、A/a $0.377449122、B/c $0.295147188、B/a $0.893342904，总 **$1.673449116**；spool 独立聚合与 DB 一致。 |
| 11 | 最终报告 §3.2：B 多耗 2.4–2.7× 轮次与费用 | FAIL | 费用比 c=2.7453、a=2.3668，近似成立；**轮次比 c=125/91=1.3736、a=319/234=1.3632，总 444/325=1.3662**，不是 2.4–2.7×。 |
| 12 | 最终报告 §3.3：工件记录 A 3/40、B 2/40；可执行 7/40、4/40 | PASS（仅记录重放） | v3 JSONL 80 行逐行聚合精确一致；封闭文件 10 行，1 正确、1 可执行。不是对 scorer 正确性的背书。 |
| 13 | 上述 7.5%/5.0% 是可靠正确率 | FAIL | 12 个已执行成功 SQL 用只读产品库重放：**A dev-02/dev-08，B dev-08/dev-26** 原始查询结果与 gold 一致，却因 Decimal→str 判错。仅消除该表示差异即 **A 5/40=12.5%、B 4/40=10%**，封闭仍 1/10；详见 F1，尚不是修订后的完整 validator 验收。 |
| 14 | 最终报告 §3.3：tokens 170,696→64,046（−62.5%） | PASS | v3 `total_tokens` 合计一致，降幅 **62.4795%**。 |
| 15 | README：上下文 token −62.5% | FAIL（指标名错误） | 上述是输入+输出。`prompt_tokens` 为 **143,076→38,770，−72.9025%**；completion 为 27,620→25,276。 |
| 16 | 最终报告 §3.3：Recall A 1.000、B 0.910；检索未达门槛 | PASS（本次工件口径） | v3 逐行平均为 1 与 0.9099999999999998；统一数值表示后 B 正确率仍未比 A 高 5pp，recall 仍下降，保留门槛仍不满足。不能据此推出一般统计优越性。 |
| 17 | 最终报告 §3.3：50 题双验证 | PASS | 原 builder 验证逻辑在只读连接、临时输出目录下完成：development 30、regression 10、closed 10；`all 50 reference queries validated (execute + AST policy)`，exit 0。双验证不包含评分器一致性验证。 |
| 18 | HANDOFF:759：v1/v2 零模型调用零成本；产品全程 $0.059496 | FAIL | v1 80 行费用为 0；**v2** 80 行中有非零 usage/cost，A **$0.008596062**、B **$0.003426270**，共 **$0.012022332**。四份 JSONL 总 **$0.071519040**，v3+closed 才是 $0.059496708。 |
| 19 | 最终报告 §4：实际/平台终账约 88 元，55% | FAIL（实际实扣未闭合） | 可重建约 88 元的混合估算；后期 simulator 仍 pending balance check。不存在本轮可核验的全期平台终账，不能把估算改称实扣。详见 §4。没有证据说明超 160 元。 |
| 20 | HANDOFF:767：agent 累计约 $7.94 / 56.1 元 | FAIL | 仅 c+a+消融+v3/closed 四项就 **$11.340299196**；漏了 c 批 $3.456748974 等。现存 BIRD spool 与全部产品 JSONL 合计 **$11.781649242**，也只代表留存遥测，不含不可见/未记录调用的完整账单。 |
| 21 | 最终报告 §5：80 commits、34 节日志、38 节账本 | PASS（限定计数） | 增量 80 commits，含基线整个历史 81；日志编号 §1–§34 另有 §0；账本 **38 个顶层键**，并非 38 笔可直接相加的支出。 |
| 22 | 0006/0007 屏障视图列白名单和最小授权 | PASS（抽验） | 现场 `security_barrier=true`；trace/eval owner 为 ops_owner/evaluation_owner；列分别 8/19/5，与迁移源一致。`agent_reader` 读 eval 基表 false、读 eval view true。未把 JSON 内自由文本自动视为已脱敏。 |
| 23 | 最终报告 §4：全部付费调用零 peak 泄漏 | FAIL（全称证据不足） | **现存 BIRD spool 5,273 条 model_turn 均标 off_peak**、金额 $11.710130202，支持这部分遥测。产品 JSONL 无逐调用 band/timestamp，simulator 无完整遥测；无法独立证明“全部调用”。 |

### DB 汇总原始结果

| 实验族 | attempts | succeeded | failed | infra | 不同 task/mode | 非空 reward | reward>0 | 有 agent_cost 的行 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| a4-c-* | 305 | 300 | 4 | 1 | 300 | 300 | 0 | 0 |
| a4-a-* | 225 | 223 | 2 | 0 | 225 | 223 | 0 | 225 |
| ablation-repair-* | 120 | 120 | 0 | 0 | 60（两条件复用） | 120 | 0 | 120 |

c 主批 b04 为 21 succeeded + 4 failed；sweep 是这四题的成功重跑。b01 另有 1 infra 后重试成功。a b02 为 22，另有 v4check 3，合成该批 25。故不能从“12×25 + sweep”推得 304 个成功。

SQL 复核入口（只读；另执行了按 experiment_id/mode/status 的细分）：

```sql
SELECT a.experiment_id, a.mode, a.status, count(*) AS attempts,
       count(r.reward) AS scored,
       count(*) FILTER (WHERE r.reward > 0) AS positive,
       count(*) FILTER (WHERE r.phase1_passed) AS p1,
       sum((a.telemetry->'agent_cost'->>'amount')::numeric) AS agent_usd,
       sum((a.telemetry->>'agent_turns')::int) AS turns
FROM eval.task_attempt a
LEFT JOIN eval.task_result r USING (attempt_id)
GROUP BY 1,2,3 ORDER BY 1,2,3;
```

通过 `docker exec commerce_analyst_product_postgres psql -U commerce_admin -d commerce_analyst -X -P pager=off -c "<SELECT>"` 执行。没有读取连接秘密。

消融 JSON 复算的两个确切文件：

- `outputs/bird-eval/episodes/716f6410-d4bb-4341-894c-719c57976f48.json`：A/c，submit 1 次，反馈 `passed=[false]`、reward 合计 0，顶层 P1/P2=false。
- `outputs/bird-eval/episodes/a8261499-49a8-4abc-ace4-18fb7697f994.json`：B/c，同题，submit 2 次，`passed=[false,false]`、reward 合计 0，顶层 P1/P2=false。

只对 `results[0].tool_trajectory` 中 `tool == 'submit_sql'` 的官方允许反馈做聚合；未访问 GT。

## 3. 代码 findings

分级：P0 为需立即阻断的重大缺陷；P1 为影响结果/安全停止/产品正确性的风险；P2 为应补齐的边界和维护建议；P3 为观察与已知限制的处置意见。这里只把新复现的问题列为 findings；CASE 已披露拒绝、c 多文件欠记等不冒充新发现。

### P0 缺陷

本轮未证实 P0。以下 P1 足以阻止原样发布收官数字，但不把有限样本发现升级为已发生的重大事故。

### P1 风险

#### F1. 产品评分两侧数值表示不同，产生四个已观测假阴性（新增代码）

位置：`src/commerce_agent/product_eval/scoring.py:22`；`scripts/run_product_eval.py:157`、`:189`；数据边界 `src/commerce_agent/query_engine/_postgres.py:46`。

gold 用 psycopg 直接取 Decimal，agent 用 QueryEngine，Decimal 被转成字符串。scorer 对字符串原样保留，却把 Decimal 量化成六位字符串。因此相同 SQL 的 `Decimal('1.20')` 与 `'1.20'` 比较为 false。将 gold 改成 builder 同款读取方式，只修好了 gold 自身 digest，未修 agent/gold 的跨边界比较。

最小复现（已执行）：

```python
from decimal import Decimal
from commerce_agent.product_eval.scoring import results_match
from commerce_agent.query_engine._postgres import _normalize_scalar
assert results_match([{'v': _normalize_scalar(Decimal('1.20'))}],
                     [{'v': Decimal('1.20')}]) is False
```

只读重放 v3 与 closed 中全部 12 条 `executed=true` 的保存 SQL：A dev-02/dev-08、B dev-08/dev-26 在原始类型下匹配，经 `_normalize_scalar` 后不匹配，与工件 false 精确一致。其他 8 条原判不变。由此得到仅消除此错误的 5/40 vs 4/40。

建议：定义两侧共享的带类型标量规范，区分文本与数值，避免把所有“看起来像数字”的字符串盲转；加“同一参考 SQL 分别走两条执行路径必须匹配”的边界验证。修正后用保存 SQL 免费重评分，保留原报告版本与更正说明，不需重发模型请求。

#### F2. 每行单元格排序抹去列对应，且无条件行排序违背有序题契约（新增代码）

位置：`src/commerce_agent/product_eval/scoring.py:41`、`:47`；规格 v0.3:496 要求“集合、Top-K 和排序按任务声明比较”。

`normalize_rows` 同时排序每行的 cell 和全部 rows；不是仅允许 SQL 别名不同，而是允许每一行采用不同列置换。已执行：

```python
from commerce_agent.product_eval.scoring import results_match
assert results_match([{'a':1,'b':10}, {'a':20,'b':2}],
                     [{'a':1,'b':10}, {'a':2,'b':20}]) is True
```

这里不存在一致的全表列置换可以把两结果对齐。另对要求按年月升序的题（如 closed-01/10），完全反序结果也会被判相同。当前未发现 5 个原正例受此影响，不声称已有对应假阳性；但 validator 边界确实不成立。

建议：冻结列位置/列映射，按题声明决定有序序列或无序多重集；不要逐行自行配对列。

#### F3. SSE 使用 attempt.sequence 推进 run cursor，稳定重发尾事件（新增代码）

位置：`src/commerce_agent/api/sse.py:128`，对照 SQL `event_cursor` 的 `:48` 与 `events.py` 的 `cursor`。

读源按 run cursor 过滤，响应 id 也用 cursor，但发送后保存 `event.sequence`。单 attempt 已有 off-by-one：两个事件 `(cursor,sequence)=(1,0),(2,1)`，下一轮继续查 `cursor>1`。跨 attempt 的 sequence 重置会重放更多历史。

复现：用遵循 `event.cursor > after_sequence` 的内存事件源，仅提供上述两条记录；连续取生成器 5 个块，实际 **id=[1,2,2,2,2]**，源查询参数 **[-1,1,1,1]**。无 DB、无网络。现有 `tests/unit/api/test_sse.py` 的 FakeSource 按预设页面 pop，不按 cursor 过滤，未覆盖该路径。客户端去重可掩盖 UI 重复，不能消除每次轮询重复传输或大历史分页饥饿。

建议：推进同一游标语义，并用真实过滤语义测试多次轮询、200 条以上分页与跨 attempt 恢复。

#### F4. 停止信号只在创建任务阶段检查，排队任务仍会开始执行（新增代码）

位置：`src/commerce_agent/evaluation/runner.py:141`、`:204`。

`run()` 没有 await 地为整份清单创建 Task，任务在 semaphore 排队；拿到 semaphore 后不再检查 stop。停止时的 grace period 于是也允许启动未执行的新 episode，与“停止领取新任务”不符。

复现：内存 store、concurrency=1、五个 synthetic task；第一个 executor 设置 `stop_event` 后 `await asyncio.sleep(.01)`，然后返回 succeeded。**五个 task 全部执行，summary 为 succeeded=5、stopped=true**。不是外部 shell 杀进程的已知坑，也不是并发 4 特有问题；升档会扩大在途影响。

建议：领取执行资格后再次检查停止状态，未启动的任务留给下次恢复；grace 只等待已在执行的任务。

#### F5. SIGINT grace 取消 executor 不终止官方子进程（新增路径，区别于已知 shell 坑 58）

位置：`src/commerce_agent/evaluation/_official.py:102`–`:113`，调用者 `runner.py` 的 `_mark_abandoned()`。

只在 `TimeoutError` 分支 kill；Runner `episode.cancel()` 产生 `asyncio.CancelledError`，直接退出 executor，未 kill/wait。DB 已标 interrupted 不代表模型调用停止，恢复还可能与原子进程重叠。

复现：替换 `asyncio.create_subprocess_exec` 为可记录 `kill()` 的 fake process，`communicate()` 等待事件；启动 `execute()`，进入 communicate 后 cancel 并 await。实测 **`CANCEL_KILLED_PROCESS False`**。未启动真实子进程/模型。已知坑 58 讲 detached shell；这是生产 executor 自身取消清理缺失，不能靠 timeout 修复或双时点手工观察替代。

建议：把取消/异常退出也纳入进程生命周期清理，等待退出后再完成中断处理；并明确已经发出的服务端请求如何停止，不把 kill 客户端等同于撤销远端调用。

#### F6. gather 吞掉状态/存储异常，CLI 仍可能 exit 0（新增代码）

位置：`src/commerce_agent/evaluation/runner.py:163`、`:175`；`src/commerce_agent/evaluation/cli.py` 的 `_async_main()`。

`return_exceptions=True` 的返回值未检查。`_run_one` 特意重新抛出的 EvalStateConflict，以及 register/finish 的存储异常，被变成 gather 元素而非传播至 CLI 的错误处理。

复现：内存 store、单任务 executor 直接 `raise EvalStateConflict('synthetic_state_conflict')`。`await runner.run()` 正常返回 **attempted=0、unfinished=1、stopped=false**；CLI 按此会选择 exit 0。这不同于已知坑 69（completed 含 failed）：这里执行状态冲突本身被静默吞掉。

建议：逐项检查 gather 结果，停止调度并传播状态/持久化错误；summary/退出码必须区分“正常终态失败”和“Runner 自身未完成”。

#### F7. SQL 身份列防护可被括号/表达式包装绕过（新发现，基线已存在）

位置：`src/commerce_agent/query_engine/_ast_policy.py:247`。

只拒绝投影最外层为 Column 且名称是 raw identity 的节点；括号、拼接、COALESCE、MIN 后不再是 Column，却能原样输出 ID。已执行 AstPolicy 验证，以下全部 ACCEPT：

```sql
SELECT (seller_id) AS x FROM retail.sellers LIMIT 1
SELECT COALESCE(seller_id, '') AS x FROM retail.sellers LIMIT 1
SELECT seller_id || '' AS x FROM retail.sellers LIMIT 1
SELECT MIN(seller_id) AS x FROM retail.sellers
```

本轮只做 AST 复现，没有输出真实 seller ID。QueryEngine 结果边界只做类型转换，不替代这一隐私检查。`git diff d87768a...HEAD -- .../_ast_policy.py` 确认增量仅增加布尔豁免，这个漏检在基线即存在；作为用户要求的重点边界审查报告，不算本轮作者引入的回归。

建议：对投影做来源/表达式血缘检查，允许已审核的不可逆统计（如 COUNT）与 opaque ref，拒绝仍可直接恢复 raw ID 的包装；不要一概禁止 WHERE/JOIN 中身份列。

### P2 建议

#### F8. SSE 的 row_number 不是持久游标，迟到事件能改变已有 id（新增代码）

位置：`src/commerce_agent/api/sse.py:48`。

即使修正 F3，按 `(occurred_at, attempt_id, sequence)` 每次重算 row_number 仍不能保证 append-only 游标。TraceEvent 只验证时区，没有保证 occurred_at 与提交顺序一致。

复现方式（无需写库）：两次以 VALUES 构造源集合。第一次仅 A(t=20)，rank=1，客户端保存 1；第二次新增 B(t=10)，rank 变 B=1、A=2；过滤 `cursor>1` 只返回已见的 A，B 永久漏发。两个相同时间戳、UUID 顺序不同的 attempt 也可触发。日志 §15 曾以改 fixture 时间戳规避排序问题，不能作为持久 cursor 正确性的证明。

建议：落库时分配稳定、不可重新编号的 run cursor，并定义提交可见性顺序；补迟到/同时间戳/恢复 attempt 的重连验证。

#### F9. 单 spool / 单候选快捷路径完全跳过时间窗口（新增代码）

位置：`src/commerce_agent/evaluation/spool_importer.py:280`。

`len(group)==len(candidates)==1` 直接归属，未调用 `_in_window`。使用相同 experiment/task/mode、时间早三天的 synthetic SpoolUsageRecord 与今天的 AttemptRow，`assign_attempts([record],[attempt])` 实测 **assigned=1**。传入某个 run 的局部 attempt 列表或处理旧残留时，能把明显不属于该窗口的费用认领为当前 attempt；与“时间消歧、绝不猜”声明不符。

建议：唯一候选也检查窗口和稳定身份；缺时间/冲突应显式分类。与已知坑 79 的多文件欠记是独立缺陷。

#### F10. harness 用字符串前缀额外拒绝合法只读 CTE/注释（新增代码）

位置：`src/commerce_agent/product_eval/question_eval.py:163`。

`sql.lstrip().upper().startswith('SELECT')` 比 QueryEngine 的 AST 策略更窄。以合法 ToolCallOutput 包装下列 SQL，AstPolicy 两条均 ACCEPT，`parse_sql_candidate` 两条均抛 `not_select`：

```sql
WITH x AS (SELECT COUNT(*) AS n FROM retail.orders) SELECT n FROM x LIMIT 1
/* comment */ SELECT COUNT(*) FROM retail.orders
```

尚未从工件证明其造成特定失败比例，不把所有 `agent_SqlCandidateError` 都归因于此。建议把语法/只读判断交给统一 AST policy，harness 只做响应契约解析。

#### F11. 聚合判定搜索整棵树，子查询/窗口 COUNT 让明细绕过 LIMIT（新发现，基线已存在）

位置：`src/commerce_agent/query_engine/_ast_policy.py:397`。

以下两条无 LIMIT，却均 `is_aggregate=True`、ACCEPT：

```sql
SELECT order_status, (SELECT COUNT(*) FROM retail.customers) AS n FROM retail.orders
SELECT COUNT(*) OVER () FROM retail.orders
```

外层仍可产出每订单一行。PostgresExecutor 的 1,000 行/5MB 上限仍在，因此不是无限结果外泄，但 AST 明细门失效，查询会到运行期才失败。建议按最外层输出粒度判断，区分子查询聚合与 window aggregate。

### P3 备注与用户指定重点的处置意见

1. **spool 坑 79 应进入库级实现与测试，不能长期留在 gitignored 导入变体。** 本轮 DB 与 spool 证明消融修正结果成立，不重复上报“消融仍欠记”。但标准 `assign_attempts` 仍一对一，A4 c 的 305 attempt 当前 agent_cost 全 NULL，eval UI 不能代表总账。应在库内按可验证 attempt 窗口聚合多 session、保留来源集合与幂等标识；不能只按三元组把所有重试混合。跨 price band 要保留分项，不能强行套一个 band。
2. **0006/0007 外层列白名单与 ACL 抽验通过。** trace `safe_summary` 是完整 JSONB，屏障视图不是内容脱敏器；这里依赖写入端的 Trace 契约。未发现本轮有新的数据库授权绕过，未以“安全屏障”一词推定自由文本安全。
3. **`bird_system_agent/server.py` 新 env 旋钮**：`BIRD_ABLATION_CONDITION` 仅经 strip/lower 后等于 `a` 才启用，默认保留 B；消融原始反馈证实 A/c 样例只 submit 一次。未发现新功能缺陷；拼写错误会静默退回 B，建议配置验证并将实际条件纳入可导出的实验 manifest。不得把未做 env live 重启实验写成已验证。
4. **并发 2→4**：新 cap 与同题双模式拒绝逻辑在场，现有 unit suite 通过；未做付费吞吐复测，所以 2.3× 是历史批次观察，不是本轮证明的普遍性能结论。F4–F6 是更优先的控制风险。

### AST 运算符/构造检查矩阵

以下是本轮对用户列举构造及相邻节点族的确定性边界枚举，不宣称穷尽 PostgreSQL 全部语法。示例均只调用 `AstPolicy.validate`，不执行真实身份投影。

| 构造 | 实测结果 | 解释 |
|---|---|---|
| AND / OR / NOT | ACCEPT | 本次豁免有效。 |
| IS NULL / IS NOT NULL / IS DISTINCT FROM | ACCEPT | 未发现同类误杀。 |
| IN / NOT IN（常量列表） | ACCEPT | 未发现同类误杀。 |
| BETWEEN / NOT BETWEEN | ACCEPT | 未发现同类误杀。 |
| LIKE / ILIKE / LIKE ESCAPE / SIMILAR TO | ACCEPT | 未发现同类误杀。 |
| EXISTS（不相关子查询） | `function_denied` | 仍被 sqlglot Func 分类捕获；需明确是否应支持此运算构造。 |
| ANY(ARRAY[...])、正则 `~` | `function_denied` | 未豁免；可能是有意收紧，未直接定性安全 bug。 |
| CASE、CAST | `function_denied` | CASE 已在产品报告披露，非新 finding；需明确能力边界。 |
| 顶层 UNION / INTERSECT / EXCEPT | `root_not_select` | 根节点类型拒绝。 |
| SELECT 包裹 UNION 子查询 | ACCEPT | 与根节点拒绝不一致，但本轮未发现借此执行写入或访问禁表。 |
| DISTINCT ON | ACCEPT | 未发现同类误杀。 |
| 相关 IN 子查询引用外层 alias | `column_denied` | scope 只识别本层 sources，存在保守误杀；不把它称为绕过。 |
| CTE | AstPolicy ACCEPT | harness 却拒绝，见 F10。 |
| COUNT FILTER | ACCEPT | 普通过滤聚合通过。 |
| COUNT OVER、外层带聚合标量子查询 | ACCEPT 且标成 aggregate | 明细 LIMIT 漏检，见 F11。 |
| COALESCE / LOWER / MIN 等 | 白名单允许 | 结合身份列可绕过投影门，见 F7。 |
| schema-qualified `public.sum(price)` | `function_denied` | 本例未发现借 schema 限定绕过白名单。 |

## 4. 文档一致性差异清单

### 4.1 关键口径冲突

| 位置 | 差异 | 应采用的表述/处理 |
|---|---|---|
| 日志:416、ledger:332 ↔ DB | c “304/304 succeeded”重复计算 sweep；ledger `episodes_done=75` 又未刷新。 | 300 个任务完成；305 attempts 中 300 成功、4 failed、1 infra；另列重试。 |
| README:19、面试速查 ↔ 最终报告:36 ↔ DB | 527/529、0/627 与 0/525、0/645 不同；NULL 分数被含混纳入零分分母。 | 固定 attempt/任务/有效评分三个字段；A4 有效评分 0/523，含消融 0/643；另列未评分失败与重复尝试。 |
| 最终报告:41、日志:443 ↔ ledger 四格 | 费用倍率被复制成轮次倍率。 | 费用约 2.37–2.75×，轮次约 1.36–1.37×。 |
| 最终报告产品表 ↔ 保存 SQL 重放 | 7.5%/5.0% 是有 scorer 缺陷的旧标签。 | 发布可追溯更正；仅类型修正后 12.5%/10%，最终 validator 仍需列/排序契约修订。 |
| README:21、产品报告 §3 ↔ JSONL | total token 被称为上下文 token。 | 总 token −62.48%；输入 token −72.90%。 |
| HANDOFF:759 ↔ v2 JSONL | “v1/v2 零调用”与 v2 非零 usage/cost 冲突。 | v1 零费用；v2 作为失败调试运行保留并计入 $0.012022332。 |
| HANDOFF:767 ↔ ledger c/a/消融/产品 | agent 累计 $7.94 漏 c 批；平台口径 88.1 实为估算。 | 分开记平台已核对、agent 遥测估算、sim 待核对，不混称终账。 |
| HANDOFF 头部/§0/§3 ↔ §2.37/§5/§8.14 | 09-16 时间、ready-for-closure、下一步消融/报告，与已收官状态并存。 | 单一当前状态区刷新，旧条目标为历史，不继续指导新会话执行已结束工作。 |
| ledger:407 | `32.4 + 43.5 + 0.4 ≈75.9` 算术不成立；v4 又已经计入 a。 | 32.4+43.5=75.9；不得再加含混 0.4。 |
| ledger:476–477、:544 | 消融 running_total 仍 1.18849（仅 B）；`cap_usd=25/5` 但授权单位是元。 | final=1.673449116；金额与币种结构化。若未来读这些字段当 USD 上限会扩大授权。 |
| 日志:289 | 分项 0.029314+0.000695+0.013832 与总 0.034841 不相等。 | 分项和为 0.043841，差 $0.009；不能只重复抄总数。本轮未凭估算替换其权威金额。 |
| HANDOFF:325、ledger run g | completion 15,774 / reasoning 15,181，却称 16,384 下必死。 | 15,774 <16,384；run f 的 18,895 才能证明该轮超过旧 cap。官方默认等效不证明任何低于 64K 的 cap 必然失败。 |

### 4.2 预算重建：88 元是怎样得到的

账本混合历史余额与后续 agent 遥测：

| 阶段 | 可核验金额 | 证据性质 |
|---|---:|---|
| 历史平台读数 | 6.43+0.65+0.32 = 7.40 元 | 账本中的用户历史读数，本轮无独立平台账单；时间截至 09-15 早间。 |
| C1 系列增量 | 账本锚约 0.53 元，使基线 7.93 元 | agent 估算，sim 待核对。 |
| A4 c | $3.456748974 | 现存 spool 逐行复算，DB 未导入。 |
| A4 a（含 v4check） | $6.150604398 | DB 与 spool 相等。 |
| 消融 | $1.673449116 | DB 与 spool 相等。 |
| 产品 v3+closed | $0.059496708 | 90 行 JSONL 重算。 |
| 产品 v2，原总账漏记 | $0.012022332 | 80 行失败调试运行 JSONL 重算。 |

按历史指示汇率 7.07，把遗漏 v2 计入：

`7.93 + (3.456748974 + 6.150604398 + 1.673449116 + 0.071519040) × 7.07 = 88.19091320296 元`。

这解释了“约 88”数量级，**不是完整实际消费**：7.93 本身取约数，后期 sim 未加齐，网关拒绝响应等调用也未必进入成功 spool。账本自身另披露平台人民币/美元价列换算约 6.6667，保留 7.07 只是历史展示惯例。因此本轮能认可“按该口径估算明显低于 160”，不能认可“平台最终实扣 88.1，已全面对账”。

不能把 38 个顶层键遍历相加：其中有余额、投影、撤回修正、授权额度、累计值和分项，直接相加会重复计费。建议日后用带币种/时间窗/来源/是否结清的交易明细派生总账；本轮未改 ledger。

### 4.3 规格、实验解释与可复现性

1. **能力门例外已披露但 DoD 语气过强。** Day 7 计划 Task 7 前置是 Task 4 PASS（reward>0）；执行日志 §29/C2 已记录“能力门如实标注”并继续，故不是本轮发现的隐瞒授权。最终报告应写“经裁定在能力门未过下完成有限范围评测”，不能同时暗示全部原规格验收通过。
2. **v4 不是纯归档且未混入主结果。** ledger:436 明确三集 v4check 算入 a 的 225；而回退理由写“单一策略优先”。三集混用是已披露事实，建议在最终表分层或加脚注，不能称全部同一策略。实验 config_hash 是选题工件 hash，不能单独证明运行配置相同。
3. **单步 SQL harness 的限制有披露，但不等于原 §17.1 全部实现。** 规格:629–630 要求两条件同一 BusinessValueResolver；当前 question_eval 路径没有调用 resolver，glossary 只扩展检索索引文本，不等价于业务值解析。规格产品 50 题还包含澄清/下钻/运营题；当前 50 条均为 SQL 参考题。建议把 DoD 标为经范围收窄的 SQL A/B，而非用“题数相同”替代语义覆盖。
4. **“全量 Schema 更有效”只宜作为这次预注册选择，不宜包装成普遍优势。** 原数值错了但两条件仍只差一题；没有置信区间或跨重复实验，不支持强统计论断。失败机制也不能从零分直接归纳为全部领域知识缺口：日志同时记录 SQL 运行期质量/JSONB 键盲、adapter 历史缺陷。
5. **消融零增益不证明“失败在修复不可达层”。** 数据证明的是本样本 A/B P1 均零；规格 §17.2 明确不支持普遍语义错误修复结论。“修复环已有行为改善验证”也应区分 BIRD 官方有限修复、v4 提示行为与未接入产品 harness 的 SqlReasoner，不混为同一个已验证杠杆。
6. **“全部入库、一键复现”过强。** c 子批清单、消融拆分清单及 `outputs/product-eval/` 在现场仍 untracked；c 遥测导入修正在 gitignored 辅助脚本，标准库仍有坑 79。Day 1–2 部分未跟踪工件是已披露遗留，不作为新代码 finding；但已披露遗留与最终报告:72 的全称复现声明不能同时成立。未做 clean checkout 安装测试，不能声称具体 clean checkout 失败已实测。
7. **“80 commits”应注明增量。** 当前 HEAD 81 个历史提交，80 是相对用户基线；“34 节”“38 节”分别是业务编号/顶层键计数，不是独立证据或逐笔交易数。

## 5. 被高估与被低估的结论

- **被高估：收官数字已经全面对账、工程完整且故障原因已钉死。** 成功数重复、评分偏差、估算冒充终账和停止路径缺陷，说明测试绿灯与留痕数量不能替代端到端证据；“零分由知识缺口决定”仍是有限样本解释。
- **被低估：现有可执行 SQL 的质量与工件的审计价值。** 四个真实匹配被类型边界判错，当前可执行子集优于原正确率；而保留的 SQL、spool、episode 与 DB 记录使本轮能零付费复算并定位差异，这是值得保留的工程成果。提高这一结论的可信度不需要再烧模型预算，先修评分契约、重算旧工件并统一口径即可。

---

审查边界：以上是只读发现与文字建议，不包含任何修复提交或继续开发授权；报告不得被解读为重新开放 BIRD/产品付费评测。
