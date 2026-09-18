# CommerceAnalyst 终局裁定

日期：2026-09-18。本轮基线：`e6053fd`。结论：四项裁决均为 **ACCEPT**，审查循环关闭；签署范围为“有限范围收官 + 已披露保留”。

## 1. 增量验证表

本轮只验证最后增量及指定未跟踪 helper，沿用前三轮已确认的证据和设计裁断，不重新开启既定事项。`git log --oneline -4` 实测顺序：`e6053fd` / `cfd77f0` / `5c915ec` / `32123ef`，符合指定基线。

| 验证项 | 结论 | 证据与范围 |
|---|---|---|
| P2-A：bool 假阳性修复 | **PASS** | `src/commerce_agent/product_eval/scoring.py:96` 对 bool 参考列要求 `isinstance(value, bool)`；原 `'False'` 对 True、7 对 True 两条均返回 False。True/True、False/False、None/None 仍返回 True。 |
| bool 边界独立验证 | **PASS** | ordered/unordered 两模式 × True/False 两参考值 × 实际值 `[True, False, 'False', 'True', 'true', 'false', 0, 1, 7, None]`，共 40 个断言全过；另两模式各验证 None/None。只接受相同真实 bool，不接受字符串或数值真值性替代。 |
| 数值/文本/行序回归 | **PASS** | 上轮 §2 的 8 条独立复现全部通过：ordered 正确多行、错误单行、重复错误行；无序文本/NULL/数值重排；重复次数敏感；错误文本拒绝。另验证字符串 `'1.20'` 与参考 Decimal('1.20') 仍匹配。 |
| 评分测试文件 | **PASS** | `uv run pytest tests/unit/product_eval/test_scoring.py -q --tb=line`：**16 passed，0.63s，exit 0**。包含新 `test_bool_reference_column_requires_real_bools`。 |
| P2-B：标题与当前入口 | **PASS** | `HANDOFF.md:1` 改为“11 项全部处置（10 项修复 + F8 延期披露）”；`:6` 为 `closure-supported-with-reservations`；`:13` 已报告终态 0/643、重评分结果及混合预算，不再指示下一步执行消融/产品/最终报告。实际提交状态以本轮 git 核对为准。 |
| P2-B：当前 a 批与产品费用 | **PASS** | `HANDOFF.md:428` 为 **0/223 有效评分**，明确 225 attempts / 223 succeeded / 2 failed；`:430` 为 **$0.071519040**，包含 v2 失败轮补记。 |
| P2-B：执行分母与回归构成 | **PASS** | 最终报告 `:102` 为 **v3 11/80 + 封闭 1/10 = 12/90**；`:97` 明确新增 13 条构成为评分 7、AST 4、F3 1、F5 1。与上轮核实结果一致。 |
| P3：helper 退出码 | **PASS** | 阅读 `outputs/product-eval/rescore_saved_runs.py`：移除因普通 no-match 设置 exit 1 的逻辑，报告完成返回 0。实际重放完成 **exit 0**；helper 仍未跟踪。 |
| 保存 SQL 免费重评分 | **PASS** | 使用 `uv --env-file .env` 注入连接配置，设置 `PGOPTIONS=-c default_transaction_read_only=on` 后运行 helper：A **5/7 已执行正确（5/40）**、B **4/4（4/40）**、closed **1/1（1/10）**；dev-02 有序匹配仍正确。 |
| 默认全套测试 | **PASS** | `uv run pytest -q --tb=line`：**933 passed / 140 skipped / 2 warnings，25.02s，exit 0**。140 skipped 不算本轮执行通过，两个 warning 不导致失败。 |
| Ruff | **PASS** | `uv run ruff check src tests scripts db/migrations`：**All checks passed，exit 0**。 |

上述验证零模型调用、未起 BIRD 栈、未运行 `run_product_eval.py`、未读取 `.env` 本体、未访问 BIRD GT/evaluator-only。未修改已有代码、文档或题集，未 commit/push。本轮仅新增此未跟踪裁定文件。

## 2. 四项裁决

### 2.1 bool 修复：ACCEPT

严格 bool 是正确边界。QueryEngine 保留 PostgreSQL 的真实 bool，不存在必须把字符串还原成 bool 的传输需求。拒绝 `'False'`、`'True'`、0、1、7 等替代值，能消除 Python truthiness 引入的假阳性，并与参考侧类型锚定一致；NULL 仍按独立空值契约比较。

本轮不建议增加字符串字面量解析。若未来接入明确将布尔值字符串化的输入协议，应另行定义该协议的有限字面量解析；这不是当前修复的缺项，也不是收官前置条件。

### 2.2 文档残余核销：ACCEPT

上一轮 P2-B 的具体清单可全部销项：标题的全修表述、当前交接入口、a 批有效评分分母、产品费用补记、合并执行分母均已改正；新增回归构成也已精确化。历史带日期记录保留原值按既定策略接受。

此裁决确认指定残余清单关闭，不扩展为每个历史段落或提交前状态注记均已重写。本文验证的实际 HEAD 为 `e6053fd`，不会把交接中的提交前“待授权”注记解释为该 commit 尚不存在。

### 2.3 收官声明终裁：ACCEPT

最终报告 §1 DoD 表、§3.3 与 §9 合并阅读，可以作为“有限范围收官 + 已披露保留”签署：

- **§1：ACCEPT。** A4 部分完成、能力门 reward>0 未达、预算为混合估算均明示。c 模式“机制恢复”不被解释成 reward 能力门通过；工程交付结论限于已裁定范围及既有验证证据。
- **§3.3：ACCEPT。** 50 题三层验证沿用上轮已通过结论；本轮保存 SQL 重评分再次确认 A 5/40、B 4/40、closed 1/10。预注册判据下的路线选择成立，不外推为通用产品能力或普遍优势。
- **§9：ACCEPT。** 收官口径明确不声称“全部问题清零”；bool、指定文档残余和 helper 退出码最后增量均已验证。

签署保留以下既定边界，不重新要求本轮实现：

1. F8 持久游标未修，已有迁移方向和晚到/重排、跨 attempt 重连、导入兼容验收登记；属于待排期，并非已实施或已承诺具体日期。
2. 值匹配契约下标签非语义，单行列值互换与合法列重排不可区分；不代表指标角色语义已验证。
3. COUNT FILTER 命名身份列、CTE 派生计数身份名别名的保守误拒继续接受。
4. 预算为混合口径估算，sim 侧待核对；不是平台实扣终账认证。
5. 保存运行只覆盖一道有序题；其它有序题证据来自此前的参考自评分及回归，不冒称为模型成功运行覆盖。

### 2.4 审查循环关闭：ACCEPT

在四轮审查的既定范围内，已无“必须本轮修复”的开放项。原 findings 11 项、其后评分/策略/文档 P1，以及最后 bool/文档/helper 增量，均已获得修复验证或明确接受的延期处置；这些编号跨轮有关联，不当作互不重叠的缺陷数量相加。

F8、导入器库级聚合延期仍是实际功能保留，sim 待核对仍是财务证据保留；它们限制“可靠持久重连”“通用自动导入”和“最终实扣结算”等更强声明，但不阻断当前有限范围收官。push 与既定仓库补录属于后续发布/归档安排，不是重跑实验或继续修复的隐含授权。

因此关闭本次审查循环。关闭表示本轮必须处置项已核销，不表示延期功能已经完成，也不表示未执行的集成测试被认证通过。

签署：有限范围收官声明成立（保留已披露边界）；审查循环关闭。
