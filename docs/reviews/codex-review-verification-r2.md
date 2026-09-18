# CommerceAnalyst 二轮处置验证

日期：2026-09-18。验证对象：`5c915ec`（代码）、`cfd77f0`（文档，当前 HEAD）。

**结论：原三个代码 P1 已验证修复，当前产品评分数字及 50 题三层验证成立；可以作有保留的有限范围收官，不能宣布所有缺陷和当前态文档差异已清零。** 八项处置中，6 项 FIXED-VERIFIED、1 项 FIXED-DISPUTED（P1-4 文档仍有遗漏）、1 项 NOT-FIXED（P2-2/F8，延期披露已达标）。新发现布尔列归一化假阳性，属于本次重写的相邻缺陷，当前 50 题没有布尔参考列，未影响已核验的 5/40、4/40、1/10。

## 1. 范围、基线与执行证据

先执行 `git log --oneline -3`，结果为：

```text
cfd77f0 docs: land codex round-2 corrections in body docs, schedule f8, disclose day-4 backfill
5c915ec fix: codex round-2 p1 hardening (scorer compare rewrite, nested-star denial, bank row-order contract)
32123ef docs: apply codex review corrections to all closure claims
```

用户示例中的 `32123ec` 按实际上一版 commit `32123ef` 对比。未改写已有文件、题集或历史；仅新增本文，保持未跟踪。未读取 `.env` 本体、未访问 BIRD GT/evaluator-only、未起 BIRD 栈、未调用模型 API、未运行 `run_product_eval.py`、未 commit/push。

| 验证 | 实测 |
|---|---|
| `uv run pytest -q --tb=line` | **932 passed / 140 skipped / 2 warnings，24.84s，exit 0**；140 skipped 不视为本轮执行成功 |
| `uv run ruff check src tests scripts db/migrations` | **All checks passed，exit 0** |
| `uv run pytest tests/unit/review_response/test_codex_findings.py tests/unit/product_eval/test_scoring.py -q --tb=line` | **26 passed，3.36s**，包含整个回应回归文件及评分测试 |
| 独立最小复现 | ordered 三条、文本/NULL/数值重排、重复计数、错误文本、嵌套 star 与对照，结果见 §2 |
| 产品题集内存验收 | **50/50** SQL 执行、AST、真实 QueryEngine 结果自评分通过；50 个保存摘要均匹配；未执行会写题集的 builder |
| 保存 SQL 免费重放 | A **5/7 已执行正确，即 5/40**；B **4/4，即 4/40**；closed **1/1，即 1/10**；dev-02 有序匹配通过 |
| 产品 eval 库只读查询 | a：225 attempts / 223 succeeded / 223 scored / 0 positive；c：305/300/300/0；消融：120/120/120/0；合计 **0/643 有效评分** |
| 账本版本控制 | `git ls-files --error-unmatch outputs/bird-budget/pilot-ledger.json` 成功；本轮未重新执行全库秘密扫描，不将“已跟踪”升级为敏感值安全证明 |

参考查询使用 `default_transaction_read_only=on`。自评分程序逐题执行参考 SQL、比较 `result_digest`，再通过 `QueryEngine(AstPolicy(), PostgresExecutor(...))` 执行同一 SQL，把实际结果与参考值比较；使用 SelectorEventLoop。未直接修改评分输出或题集。

已先读 helper 源码再运行，以 `PGOPTIONS=-c default_transaction_read_only=on` 强制连接只读。helper 返回 exit 1，但所有输出符合预期：它在 A 为 5/7 时自身设置非零退出码，并非本轮发现重评分不一致。该接口问题另见 §5。

## 2. 逐项结论

| 上轮项 | 结论 | 独立证据及边界 |
|---|---|---|
| P1-1 ordered 比相邻行 | **FIXED-VERIFIED** | 原三例现为 True / False / False：正确 `[1,2]` 自匹配通过、单行 `999` 对 `1` 拒绝、重复两行 `999` 对两行 `1` 拒绝。代码改为两侧归一化行序列直接比较。10 道有序题全部经真实 QueryEngine 自评分通过。 |
| P1-2 无序先按位置配对 | **FIXED-VERIFIED** | 文本行整体逆序、NULL 行交换、纯数值逆序均 True；重复次数改变、错误文本均 False。两侧归一化在排序前独立完成，原隐性行序依赖已消除。这里验证的是原配对缺陷；新 bool 转换问题单列 §5，不以此否认原反例已关闭。 |
| P1-3 嵌套 star | **FIXED-VERIFIED** | `(s.*)`、`COALESCE(s.*,s.*)`、`COUNT(s.*)` 均被表达式 star 规则拒绝；`COUNT(*)` 及带明细 LIMIT 的 `SELECT p.* FROM retail.products p LIMIT 1` 通过。额外 `COALESCE(s,s)` 整行别名路径被列解析拒绝。未执行真实身份读取；验证不等于 SQL 安全策略的完备证明。 |
| P1-4 文档口径未落地 | **FIXED-DISPUTED** | 最终报告正文、README 指定位置、面试材料指定位置、HANDOFF §3 大部分内容及账本来源标签已改正，不再是只改附录。但 HANDOFF 当前入口仍要求报告旧状态，§3 仍有 `reward 0/225` 和产品 `$0.06`；标题还称 11 项 findings 修复。详见 §5。残余按 P2 处理，主要更正已成立。 |
| P2-1 行序元数据 | **FIXED-VERIFIED** | 实测 7 ascending、3 descending、40 unordered；dev-02/04、reg-03 正确设为 descending，reg-02 为 ascending。reg-06 的时间趋势默认时间升序已在 builder 注释中说明。`question_eval.py:211` 对非 unordered 启用顺序比较。接受当前固定题集的最小契约，见 §3。 |
| P2-2 F8 未修/登记不足 | **NOT-FIXED；披露处置已验证** | `sse.py` 仍为动态 row_number，故不能标修复。HANDOFF §5 第 5 项现包含持久 cursor 迁移、晚到/重排、跨 attempt 重连和导入兼容验收，并说明 F3 不等于可靠持久重连；最终报告 §6 同步。作为“待排期风险登记”充分，不代表已有实施日期。 |
| P2-3 interval 参考 SQL | **FIXED-VERIFIED** | dev-15/reg-03 改为先 `EXTRACT(EPOCH FROM 差值)` 再平均/换算天数；两题经真实 QueryEngine 执行并自评分成功，无 timedelta 异常。全 50 题参考标量类型实际为 Decimal/str/int，无 bool。与 32123ef 对比只这两题 gold_sql/摘要变动，其余 SQL、全部题面不变。 |
| P2-4 回归缺口/Day 4 披露 | **FIXED-VERIFIED** | 同生成器 F3 测试连续两次 yield 后第三次超时；F5 fake process 验证 kill、wait 与 CancelledError 重抛，专项通过。新增数量为评分 7、AST 参数化 4、F3 1、F5 1，共 13。HANDOFF §2.39 第 7 项、日志 §36 第 7 项明确“89390d8 有意补录，既有本地测试入库而非新增覆盖”。 |

原评分复现可用以下调用独立复核（仅内存，无模型/DB）：

```python
from commerce_agent.product_eval.scoring import results_match as m
assert m([{'v':1},{'v':2}], [{'v':1},{'v':2}], ['v'], ordered=True)
assert not m([{'v':999}], [{'v':1}], ['v'], ordered=True)
assert not m([{'v':999},{'v':999}], [{'v':1},{'v':1}], ['v'], ordered=True)
assert m([{'k':'B','v':2},{'k':'A','v':1}],
         [{'k':'A','v':1},{'k':'B','v':2}], ['k','v'])
assert m([{'v':None},{'v':1}], [{'v':1},{'v':None}], ['v'])
assert m([{'v':2},{'v':1}], [{'v':1},{'v':2}], ['v'])
assert not m([{'v':1},{'v':2},{'v':2}],
             [{'v':1},{'v':1},{'v':2}], ['v'])
assert not m([{'v':'B'}], [{'v':'A'}], ['v'])
```

## 3. 三个设计决策的明确裁断

### 3.1 标签非语义、一致置换：接受有限值匹配契约，不要求为本轮强制增加字段角色

**接受披露方案作为本次 SQL 结果值等价评分的契约。** 代码模块说明及最终报告 §8.3 已明确标签不具语义、单行换位不可区分。任意 SQL 别名/投影重排本来就可表达等价关系，不能仅因没有逐题字段角色就宣布值等价评测无效。

边界必须保留：它不能验证“指标标签与数字绑定正确”，也不是最终自然语言/图表答案的完整业务正确性。例如实际 `{'revenue':10,'cost':100}`、参考 `{'revenue':100,'cost':10}` 仍可通过；在现行无语义标签契约下这是被主动接受的等价，不再列为未披露实现缺陷。若产品正确性要求分清收入与成本字段，届时必须加入角色映射/输出 schema 等语义约束。不能把本次正确率用作那种更强能力的证据。

这是一次评分口径显式化，不是新一轮模型实验；保留原输出、说明重评分版本与限制是必要的。当前报告已区分原始执行数与重评分数，满足该要求。

### 3.2 COUNT 两类误拒：接受保守限制，不要求本轮实现血缘

**接受。** 两条原误拒 SQL 仍被拒绝，实际行为与源码注释、最终报告 §8.2 一致：COUNT FILTER 谓词中的命名身份列，以及名为 seller_id 的 CTE 派生计数会误拒。它们降低表达能力，不会把身份值误当安全输出放行。可通过当前支持的查询写法规避，且 50 题参考查询均通过。

因此允许其作为已披露功能限制延期；不把 COUNT-only 描述为完整通用血缘分析，也不要求为了完备支持所有 SQL 聚合形式而扩大本轮范围。

### 3.3 行序字符串枚举：接受当前固定题集；不要求重复存储所有排序键

**接受最小修复。** 当前实现并不按字符串决定怎样排序，而是用枚举决定是否逐行比较参考 SQL 的输出序列；排序键/方向已由题面和参考 SQL 提供。对冻结且已复核的 50 题，无需再复制一套排序键元数据才能构成验收闭环。

本轮逐题查看 10 道 ordered 题的题面和 ORDER BY，并只读检查当前参考结果按实际 ORDER BY 键无并列（各题 sort_key_ties=0）。reg-06 的“趋势按时间读”属于明示的解释约定，接受。因而没有证据表明当前题集因缺少 tie policy 而误判。

限制：对未来出现相同排序键的结果，严格逐行相等可能误拒合法并列重排；混合方向也不能靠单一 ascending/descending 标签完整表达。届时应增加 tie-group 比较或冻结唯一的次序约定。不能把当前自评分 50/50 当作任意未来题集的排序语义证明。

## 4. 对最终报告 §1 / §3.3 / §8 的裁断

| 部分 | 裁断 |
|---|---|
| §1 A4 范围、能力门数字 | **成立**：c 300 完成、a 223 完成、0/643 有效评分与本轮 DB 相符；未完成范围明确。表内“能力门恢复”✅应理解为 c 模式机制恢复，不能解释成 reward 门通过，因为同表已明确能力门 ❌。 |
| §1 预算 | **限口径成立**：已经写明混合估算、非平台终账。账本 7.93 来源修正为 7.40 平台核对 + 0.53 agent 估算，公式约 88.19 正确；sim 待核对仍是客观边界。不能作最终平台实扣小于 160 的确认。 |
| §1 Day 4/Day 5 等既有交付 | **本轮不重新全量认证**：本轮针对八项审查处置，默认 suite 通过但 PG 集成仍跳过；没有重启 BIRD 做隔离或在线能力验证。沿用历史证据不能写成本轮新跑通过。 |
| §3.3 产品实验 | **成立，限已披露值匹配/单步配置**：50/50 三层验证、A 5/40、B 4/40、closed 1/10 均重现。现有预注册判据对 B 的否决不因重评分改变。不能外推为所有小 Schema、全产品链路或所有语义标签都优于检索。 |
| §8.1–§8.4 技术处置与局限 | **主要成立**：三个原代码 P1 修复、两题 SQL/行序、COUNT 保守限制、单行歧义、F8 未修均与现场一致。十三条是本轮全部新增回归，不是十三条全属评分边界。 |
| §8.5“当前态全部改用更正后口径” | **尚不成立**：见 §5 的 HANDOFF 当前入口及当前状态残余。历史带日期记录不在此否决范围内。 |
| §8.6“执行 12/80” | **不成立，分母混用**：v3 是 11/80（A 7+B 4）；closed 是 1/10；合并才是 12/90。保存 JSONL 逐行 executed 求和即可复现。不影响 5/40、4/40、1/10。 |

**最终裁断：原三个代码 P1 和产品重评分/三层题集验收经复核成立；保留 F8 延后、布尔列相邻缺陷及文档残余差异，不签署“全部修复、全部当前态口径已统一”的无保留声明。** 这些保留意见不撤销已验证的有限实验结果，也不要求重跑任何付费实验。

## 5. 剩余问题与相邻发现

### P0 / P1

本轮没有新的证据表明原三个代码 P1 仍开放。P1-4 的主体更正已落地，剩余文档问题按 P2 留存；不继续沿用上一轮全面未落地的严重程度。

### P2-A：新 bool 归一化把任意非空字符串/非零数字变成 True

位置：`src/commerce_agent/product_eval/scoring.py:92`，`str(bool(value))`。

```python
from commerce_agent.product_eval.scoring import results_match as m
print(m([{'v':'False'}], [{'v':True}], ['v']))  # 实测 True，应 False
print(m([{'v':7}],       [{'v':True}], ['v']))  # 实测 True；不应把任意非零数认作布尔真值答案
```

Python truthiness 不是参考侧布尔类型校验。原 P1-2 的独立归一化与无序比较确已修好，这是重写归一化新增的类型边界问题。建议要求实际 bool，或显式定义允许的字符串字面量解析并拒绝其它类型；不要用 `bool(value)` 猜答案。增加 True/False 与字符串、0/1/其它数值、NULL 的边界用例。

当前 50 题参考结果类型只有 Decimal、str、int，没有 bool；因此本轮将其列 P2，不据此改算现有成绩。若扩展到布尔参考列，该缺陷会直接造成评分假阳性，扩题前应修复。

### P2-B：当前交接入口仍有旧口径，附带一处新分母笔误

位置与复现（直接读取相应行即可）：

- `HANDOFF.md:1` 仍称“11 项 findings 修复”，与 F8 明确未修冲突。应为已处置/其中十项修复、一项延期，而非全修。
- `HANDOFF.md:6`、`:13` 位于当前交接头部和“新会话先做什么”，仍要求下一步消融/产品/最终报告，并要求报告 `reward 0/225`、spent 75.9。它们不是带日期的历史执行日志，不能用保留历史策略解释。
- `HANDOFF.md:419` 当前 a 批状态仍写 `reward 0/225`，本轮 SQL 实测 225 attempts 仅 223 有效评分，应为 0/223 有效评分。
- `HANDOFF.md:421` 已正确标混合预算，但同一当前段仍称产品评测 `$0.06`；包含已补记 v2 失败轮应为 `$0.071519040`，若只指 v3+closed 须明确限定。
- `docs/reports/2026-09-17-final-report.md:102` 的“执行 12/80”应区分 11/80 与 closed 1/10，合并为 12/90。

建议只修当前入口/摘要，不重写历史。README:24/:53、面试材料:14/:28/:47、最终报告 §1/§3.3/§4/§5 指定的旧预算/peak/测试/正确率主问题已修复，不将其重复上报为未修。

### P2-C：F8 仍延期，但本次登记已充分

位置：`src/commerce_agent/api/sse.py:48`、`HANDOFF.md:460`、最终报告 §6 第 5 项。代码仍动态 row_number；新登记明确迁移方案、验收范围和不保证持久重连。接受风险披露，无需本轮增加实施日期才能算“登记充分”；也不能将此登记称作已实现持久游标。

### P3：重评分 helper 的退出码与验证用途不一致

位置：`outputs/product-eval/rescore_saved_runs.py:96`–`:97`。它把预期的 A 5/7 也设为 exit 1，尽管此次所有重评分数字均符合预期。建议报告脚本正常完成用 exit 0，或显式传入期望数值并在偏离时失败；“题目答错”与“复核脚本执行失败”应分开。此 helper 未跟踪，问题不影响库修复结论。

本文为零付费、只读验证结果；不包含自动修复或后续实现承诺。
