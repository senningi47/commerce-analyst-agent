# CommerceAnalyst 审查回应复核（第二轮）

日期：2026-09-18。上一轮基线：`e73cf3a`；本轮审查：代码 `89390d8`、文档 `32123ef`（当前 HEAD）。

**总评：部分处置成立，不能确认“收官声明经复核成立”。** 11 项 finding 中，8 项为 FIXED-VERIFIED、2 项为 FIXED-DISPUTED、1 项为 NOT-FIXED。默认测试与 Ruff 通过，免费重评分确实得到 A 5/40、B 4/40、封闭 1/10；但新评分器同时存在有序结果假阳性/假阴性、无序文本结果假阴性，身份列保护仍可通过包装整行通配符绕过。更正章节没有消除正文及对外材料中的旧声明，账本更正也仍有来源标签错误。“全部处置”不能等同于“全部验证通过”。

范围与方法：读取上述 commits、上一轮审查及处置材料；独立重放原最小复现及边界反例；运行默认 pytest、指定 Ruff 和新增回归/Day 4 授权测试；只读查询产品 PG；重执行产品题集参考 SQL 与 v3/closed 中原执行成功的保存 SQL。未调用模型 API、未起 BIRD 栈、未运行 `run_product_eval.py`、未读取 `.env` 本体、未访问 BIRD GT/evaluator-only、未修改已有代码/文档/题集、未 commit/push。`uv --env-file .env` 仅向只读程序注入连接配置，未输出配置值。未重跑会写题集的 builder，改为在内存中复核 50 题的 SQL、AST 和摘要。本文“参考 SQL”仅指用户授权的产品题集。

## 1. 逐 finding 复核

| Finding | 结论 | 独立验证结果与边界 |
|---|---|---|
| F1 Decimal-vs-str | **FIXED-VERIFIED** | `results_match([{'x':'1.20'}],[{'v':Decimal('1.20')}],['v'])` 返回 True。对 v3 原执行成功的 11 条保存 SQL、closed 的 1 条 SQL 重执行并按 QueryEngine 标量边界归一化，A 5/40、B 4/40、closed 1/10。此结论只确认原数值表示缺陷，不替 F2 的新评分逻辑背书。 |
| F2 逐行单元格乱配 | **FIXED-DISPUTED** | 原两行乱配反例返回 False，一致列换位返回 True，原洞已收窄。但 ordered 分支比较相邻行而非参考值与实际值；unordered 分支在重排行前先按行配对文本。详见 §2、§4 的三个独立反例。 |
| F3 SSE 尾部重发 | **FIXED-VERIFIED** | 使用同一个流生成器，源事件 cursor=1/2、sequence=0/1，连续取得块 id `[1,2]`；第三次 `anext` 在 0.02 秒超时，最后一次查询游标为 2，未重发。`src/commerce_agent/api/sse.py:131` 的推进值确为 `event.cursor`。不包含 F8 的持久性保证。 |
| F4 停止后排队任务启动 | **FIXED-VERIFIED** | 5 任务、并发 1，首任务设置 stop：实际执行 1 个、`summary.stopped=True`、内存 store 仅 1 条 attempt、unfinished=0。`runner.py:220` 在取得 semaphore 后检查 stop，注册 attempt 在检查之后。 |
| F5 取消不杀官方子进程 | **FIXED-VERIFIED** | fake process 在 `communicate` 阻塞时取消 executor：`kill=True`、`wait=True`，CancelledError 重新传播。`src/commerce_agent/evaluation/_official.py:111` 对应。没有启动官方进程或付费请求；该证据不声称能撤销远端已经在途的模型调用。 |
| F6 gather 吞状态冲突 | **FIXED-VERIFIED** | executor 抛 `EvalStateConflict('synthetic_state_conflict')`，runner 同样抛出而非正常返回。`runner.py:193`、`:196` 对 gather 结果逐项处理。 |
| F7 身份列包装绕过 | **FIXED-DISPUTED** | 原括号、COALESCE、拼接、MIN 四条命名身份列反例均拒绝；COUNT(DISTINCT seller_id)、reg-01/reg-10 参考查询仍通过。但 `COALESCE(s.*,s.*)` 及 `(s.*)` 通过 AST；前者在真实表的复合类型下能作为字符串穿过结果归一化。合成值验证见 §4。 |
| F8 SSE 持久游标 | **NOT-FIXED** | `sse.py:48` 仍按 occurred_at/attempt_id 等动态 `row_number()`。HANDOFF §3、§8.15 如实称“待排期 P2”；但用户指定的 §5 没有该项，也没有具体排期/验收安排。接受“已披露未修”，不接受“已经排期或修好”。 |
| F9 唯一候选跳过时间窗 | **FIXED-VERIFIED** | 唯一候选、遥测比 attempt 早 3 天：assigned=0、unassigned=1。快捷路径确已调用 `_in_window`。不扩大为“c 多文件库级聚合也已修复”；后者仍是独立遗留项。 |
| F10 harness 前缀过窄 | **FIXED-VERIFIED** | 合法 CTE、注释起始候选均经 `parse_sql_candidate` 解析并由 AstPolicy 放行；空 SQL 仍拒绝。只读决策交给 AST 与职责一致。 |
| F11 非本 SELECT 聚合绕 LIMIT | **FIXED-VERIFIED** | `SELECT order_status,(SELECT COUNT(*) FROM retail.customers) AS n FROM retail.orders` 与 `SELECT COUNT(*) OVER () FROM retail.orders` 均以 `detail_limit` 拒绝。确认所报窗口/标量子查询两条路径关闭，不将此测试宣称为所有 SQL 构造的完备证明。 |

## 2. 两个设计决策的裁断

### F2：一致置换可有条件采用，当前实现与全题契约均不能直接批准

一致置换要求所有行共享一个列映射，确实堵住原来的逐行任意换位；它与“冻结列映射”的目标并不天然冲突。对于题目只要求返回关系、允许任意别名与投影次序的场景，推断一个固定映射可接受，也无需强求 SQL 别名逐字一致。

但“全部列总是允许按结果值匹配”需要明确写入题目输出契约。单行时约束退化为单行单元格集合：参考 `{'revenue':100,'cost':10}`，实际 `{'revenue':10,'cost':100}` 仍返回 True。若列名/角色就是答案的语义，这会掩盖指标互换；若题目明确列标签无语义，则只能确认无标签关系等价，不能进一步声称语义标签正确。当前统一接口没有逐题区分这两种情况。

建议为题集声明返回字段的语义角色、是否忽略别名/列序、行序与并列排序规则；有标签指标使用固定角色映射，无标签关系才推断一次一致映射。不要仅凭结果数值决定字段含义。实现上还必须修复 §4 的 ordered 与 unordered 两条实际错误；它们不是设计取舍，而是违背当前函数自身契约。

### F7：COUNT-only 可以作为保守策略，但目前不是完整的身份列保护

允许命名身份列仅出现在 COUNT 内，能保留 reg-01/reg-10 分母，且相较全面表达式血缘更易审计。拒绝 MIN/拼接/COALESCE 等可恢复原身份的表达式合理。无需为了采纳上一轮建议而强行实现通用血缘系统。

保守规则也有可复现误杀：

```sql
SELECT COUNT(*) FILTER (WHERE seller_id IS NOT NULL) FROM retail.sellers;
WITH x AS (SELECT COUNT(*) AS seller_id FROM retail.sellers)
SELECT seller_id FROM x LIMIT 1;
```

两者均报 `identity_projection_denied`。第一条只输出计数，FILTER 条件却在 Count 节点外；第二条是派生计数被命名为 seller_id，按名称检查误当原始身份。这些可以作为明确披露的保守限制，或者增加有界来源判定。

更严重的是漏检：`_identity_leaks` 遇到 `exp.Column` 仅检查名字是否在四个身份名中，遇到嵌套 `s.*` 就跳过；外围 star 检查又只看投影顶层。因此 COUNT-only 的口号并不覆盖整行表达式。必须在任意表达式位置处理 qualified star/whole-row 及其来源列，再谈安全闭环。相关 P1 见 §4；本轮未查询真实身份值。

## 3. 更正后声明抽验

PASS 表示本轮相应证据支持该具体口径；FAIL 不表示所有历史执行无效。

| 声明 | 结论 | 本轮证据 |
|---|---|---|
| 919 passed / 140 skipped | **PASS** | `uv run pytest -q --tb=line`：919 passed、140 skipped、2 warnings，36.84s，exit 0。跳过的集成测试不算本轮已执行。 |
| Ruff 全绿 | **PASS** | `uv run ruff check src tests scripts db/migrations`：All checks passed，exit 0。 |
| 新增回应回归 9 条通过 | **PASS（覆盖有限）** | `tests/unit/review_response/test_codex_findings.py` 的 9 条 + Day 4 授权 7 条定向运行：16 passed，4.20s；但 F3 测试重新创建流、没有连续第三拉取，且此文件没有 F5 fake-process 用例。独立复现补足本轮验证，不代表仓库回归覆盖完整。 |
| a 批 225 attempts / 223 成功 | **PASS** | 产品库只读聚合：225 attempts，223 succeeded、2 failed，223 非空 reward、0 正 reward。 |
| c 批 305 attempts / 300 成功 | **PASS** | 产品库：305 attempts，300 succeeded、4 failed、1 infrastructure_failed，300 非空 reward、0 正 reward。 |
| A4 530 attempts / 523 有效评分 / 0 正 reward | **PASS** | 上述 a+c 合计：530/523/0；失败未评分行没有当成 reward=0。 |
| 消融 120/120，0 正 reward | **PASS** | 产品库消融族 120 attempts、120 succeeded、120 非空 reward、0 正 reward。A4+消融有效评分分母为 643，而非正文 645。 |
| 免费重评分 A 5/40、B 4/40、closed 1/10 | **PASS（限定保存运行）** | 重放 v3/closed 中原执行成功 SQL，再执行各题参考 SQL。A 正确 dev-02/08/09/14/19；B 正确 dev-08/09/19/26；closed 正确 closed-05。未把当初没有成功执行的题目改算成功。 |
| 总 token −62.5%，输入 token −72.9% | **PASS** | v3 JSONL 逐行聚合：A total=170696、prompt=143076；B total=64046、prompt=38770。降幅 62.48% / 72.90%。不是“上下文长度减少 62.5%”。 |
| v2 失败轮也有模型费用，产品总计 $0.071519040 | **PASS** | 保存 JSONL 的非空 cost 汇总：初轮 $0；v2 $0.012022332；v3 $0.050227428；closed $0.009269280。相加 $0.071519040。未将此视为平台实扣核对。 |
| 50 题“真实库执行 + AST”双验证 | **PASS（仅这两层）** | 50 条参考 SQL 全部只读执行成功、AST 通过；新 `result_digest` 与题集保存摘要全部一致。没有调用会写 data 的 builder。运行时可归一化/可评分性见下一行。 |
| 有序/无序评分契约已正确落地 | **FAIL** | 参考结果经 QueryEngine 标量转换再与自身评分：6 道 ascending 题全部 False；另有 dev-15/reg-03 的 timedelta 归一化异常，余下 42 题通过。不能由“双验证 50/50”推导出评分闭环 50/50。 |
| 6 道 ascending 的选择与题面一致且覆盖完整 | **FAIL** | dev-02/dev-04 明说金额降序、reg-02 明说年升序、reg-03 明说降序，均标 unordered；reg-06 只说趋势，未明确升序。详见 §4。 |
| F8 已披露为未修复 | **PASS** | HANDOFF:412、786 明确待排期 P2；代码仍为动态 row_number。 |
| HANDOFF §5 已充分记录 F8 排期 | **FAIL** | §5（441–446）仅列 push、补录、能力杠杆、cybermarket 恢复。F8 只有其他章节的待裁定登记，没有具体安排。 |
| 最终报告/README/面试材料数字与措辞“全面更正” | **FAIL** | 最终报告 §7 与正文、README:24、面试材料:14/28、HANDOFF 当前状态互相冲突；详见 §4。 |
| 88.19 是混合估算而非平台终账 | **PASS（定性）；来源标签 FAIL** | 账本更正节公式算术约为 88.19，但将 7.93 标成 historical platform reads；原账本:284 明示其为 7.40 平台核对 + 0.53 C1 agent 估算/遥测，sim 待核对。正文仍把约 88 当实际支出。 |
| Day 4 授权测试保留且已经书面披露回填 | **保留成立；披露 FAIL** | `89390d8` 确实新增跟踪 324 行文件，7 条测试通过；用户本轮明确同意保留。HANDOFF:768 仍只有旧 amend 移出记录，本轮 §2.38/§8.15、日志 §35、最终报告 §7 与 commit message 未记录本次保留为 Day 4 回填。 |

重评分限制：这些保存运行中的成功 SQL 未覆盖 6 道 ascending 题，故“5/4/1 数字可复现”与“ordered 分支错误”同时成立。不能据此认定修复对未来执行的评分可靠，也不能将 12.5% 对 10.0% 的小样本差异升级为普遍优越性结论。

## 4. 未解决问题（按级别）

### P0

本轮未发现 P0。

### P1-1：有序分支比较对象错误，正确答案失败、错误答案成功

位置：`src/commerce_agent/product_eval/scoring.py:111`，尤其 `:114` 的 `pairwise(normalized)`。

```python
from commerce_agent.product_eval.scoring import results_match as m
m([{'v': 1}, {'v': 2}], [{'v': 1}, {'v': 2}], ['v'], ordered=True)
# 实测 False；应 True
m([{'v': 999}], [{'v': 1}], ['v'], ordered=True)
# 实测 True；应 False
m([{'v': 999}, {'v': 999}], [{'v': 1}, {'v': 1}], ['v'], ordered=True)
# 实测 True；应 False
```

`normalized` 每个单元格是 `(reference, actual)`，代码却比较相邻整行；单行的 pairwise 为空，all 自动为 True。用 50 题参考结果做自评分，closed-01/10、dev-01/10、reg-06/08 全部失败。建议按行序逐单元格比较两侧值，并加入单行错误、多行正确、重复错误、逆序四类边界回归。

### P1-2：无序分支在排序前要求文本/空值按原行位置匹配

位置：`scoring.py:97` 的 zip，结合 `:38`–`:54` 的 `_quantize_pair`。

```python
m([{'k':'B','v':2}, {'k':'A','v':1}],
  [{'k':'A','v':1}, {'k':'B','v':2}], ['k','v'])
# 实测 False；两者为同一个多重集，应 True
```

数值配对允许先归一化、最后再排序，而文本不相等或 None 错位立即失败；因此无序模式实际部分依赖数据库行序。建议先按可靠的列类型契约各自归一化，再比较行多重集；混合类型/空值须保持类型信息，不能重新退回每行单元格集合。加入文本、NULL、重复行和整体逆序验证。

### P1-3：嵌套 qualified star 绕过身份列保护，并可穿过序列化边界

位置：`src/commerce_agent/query_engine/_ast_policy.py:248`（Column 分支只识别身份名），`:276` 起的 star 检查仅作用于顶层投影。

```sql
-- AstPolicy().validate 两条均接受；直接 SELECT s.* 则拒绝
SELECT (s.*) AS x FROM retail.sellers s LIMIT 1;
SELECT COALESCE(s.*, s.*) AS x FROM retail.sellers s LIMIT 1;
```

未执行上述真实身份读取。用只含人造值、但保留 retail.sellers 复合类型的 SELECT 验证边界：

```sql
SELECT COALESCE(s.*,s.*), pg_typeof(COALESCE(s.*,s.*))::text
FROM json_populate_record(NULL::retail.sellers,
                         '{"seller_id":"synthetic_id"}') s;
```

psycopg 返回第一列为 str，值 `(synthetic_id,,,)`，类型名 retail.sellers；`_normalize_scalar` 原样放行该字符串。匿名 VALUES 的 record 在该客户端返回 tuple、会被拒绝，但这不能保护具有命名表类型的实际路径。单纯括号的 `s.*` 在 PG 投影中还会展开原列。

影响：命名身份列包装已堵住，不等于原始身份不能输出。建议对表达式中所有 star/whole-row 引用按来源列递归判断，而不是仅检查顶层；回归应同时覆盖 AST 和合成表类型的结果边界。COUNT 的安全豁免也应建立在这一完整检查上。

### P1-4：“全面更正”与当前正文冲突，外部读者仍会获得旧结论

可复核位置：

- `docs/reports/2026-09-17-final-report.md:18` 仍为 0/645；`:49`、`:50` 仍为 A 3/40、B 2/40；`:62` 写实际约 88 元；`:63` 写全部付费调用零 peak 泄漏；`:69` 仍是 907 测试。`:86`–`:90` 才宣布更正。正文没有标记这些为撤回的历史口径。
- `README.md:24` 仍写“实际支出 ≈88”“peak 零泄漏”，`:53` 仍为 907。
- `docs/interview-prep.md:14`、`:28` 仍写全程零 peak 泄漏、88/160 收官；`:52` 又改成混合估算和 sim 待核对，同一材料内部矛盾。
- `HANDOFF.md:408`–`:410` 当前状态仍保留旧产品成本、0/645、7.5%/5.0%；`:778` 的旧终账也未明确撤回。历史带日期记录可以保留，但“当前状态”不应继续当权威复述。
- `outputs/bird-budget/pilot-ledger.json:565` 把混合来源 7.93 错称平台读数，与同文件 `:284` 冲突；`:566` 的 all corrections applied 不成立。

建议直接更正当前正文与对外摘要；历史执行日志保留原值但标明被 §35 更正。用一张定义清楚的最终口径表区分 attempts/tasks/scored、平台实扣/遥测/估算及 agent/sim 范围。无需重写历史 commits。

### P2-1：题集行序元数据不完整，六题名单也非全为明确升序

位置：`scripts/build_product_question_bank.py:27`–`:29`；`data/product-eval/development.jsonl:2`、`:4`，`data/product-eval/regression.jsonl:2`、`:3`、`:6`。

当前 6 ascending / 44 unordered。明确要求排序却标为 unordered 的至少有 dev-02、dev-04、reg-02、reg-03。reg-06 的题面只是“按年和季度……趋势”，默认时间升序可理解，但不支持注释所说“题面声明升序”。reg-08 明说按年与支付方式排序，默认升序可以接受。

建议逐题声明 ordered 与排序键/方向，含降序和并列值；不要只用名为 ascending 的开关承载所有有序要求。题面及输出协议应一起冻结。与 e73cf3a 对比，50 题的 question/gold_sql 均没有净变化；本轮主要更改的是元数据及摘要，不能把元数据加入视为题面已经补齐。

### P2-2：F8 仍未修，只有待排期登记

位置：`src/commerce_agent/api/sse.py:48`；`HANDOFF.md:412`、`:441`、`:786`。

动态 row_number 的稳定性问题仍在。明确延后可接受，但 §5 没有相应项。建议记录持久 cursor 迁移、已有事件处理方式，以及晚到事件/跨 attempt/断线重连的验收条件；在此之前不能把 F3 修复表述成“可靠持久重连”已经完成。

### P2-3：两道产品参考 SQL 的“天数”仍为 interval，双验证未覆盖产品结果类型

位置：`data/product-eval/development.jsonl:15`、`data/product-eval/regression.jsonl:3`；`src/commerce_agent/query_engine/_postgres.py:40`。

两题均使用 `AVG(timestamp2 - timestamp1) / 86400.0`。PG 中 interval 除数仍为 interval，不会变成数值天数；合成 `SELECT pg_typeof(INTERVAL '1 day' / 86400.0)::text` 返回 interval。实际参考查询返回 timedelta，`_normalize_scalar` 抛 `Unsupported query result type: timedelta`。因此原样复用正确参考 SQL 也无法通过当前产品序列化边界。

这是前版本已有、此次参考自评分新暴露的问题，不归罪于本轮新增元数据；但它限制“50 题有效评分基准”的声明。建议把时间差明确转为数值秒再换算天数，并把参考查询经真实结果归一化后自评分加入题集构建验收。仅 DB 可执行 + AST 合规不够。

### P2-4：回归覆盖与 Day 4 回填留痕需要补齐

位置：`tests/unit/review_response/test_codex_findings.py:230`；`tests/unit/product_eval/test_day4_authorization.py:1`；`HANDOFF.md:768`。

F3 新测试每次 `_pull` 创建新生成器，从客户端保存的 cursor 开始，未触及同一生成器两次 yield 后的内部推进；原缺陷可在这种测试形式下漏检。本轮用同一生成器独立验证通过，因此不撤销 F3 修复结论，但建议固化该复现。该回归文件也没有 F5 取消 fake-process 测试，F2 未覆盖排序正例/单行反例。

Day 4 授权测试保留有价值，7 条通过，且用户当前明确同意；不建议移除。需要在当前交接/变更记录补一句“89390d8 有意补录 Day 4 遗留授权测试”，区分“已有本地测试进入版本控制”与“本次新编写测试”。上一轮工作区默认 suite 已包含它，不能将其入库再计成新增行为覆盖。当前文档只有旧 amend 移出记录，尚未完成用户所说的披露。

## 5. 验证边界与最终裁断

本轮默认 suite 为 919/140，专项为 16 passed，Ruff 通过；绿色测试是真实证据，但未覆盖本文新反例。产品 JSONL 和题集未改写，免费重评分为内存复核；没有把历史记录的 correct 字段原地替换，也没有借重新运行模型改变样本。

原 F3/F4/F5/F6/F9/F10/F11 以及 F1 的修复证据足够；一致列映射与 COUNT 保守策略可以继续使用，但必须修复实际错误、补全适用契约。F8 可按已披露 P2 延后，不能算已修。**保留意见是实质性的：当前仍有 P1 评分与身份输出缺陷，且“文档全面更正”不成立；因此不签署无保留的收官确认。**
