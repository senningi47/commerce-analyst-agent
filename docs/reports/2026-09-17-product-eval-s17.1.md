# 产品评测 §17.1 执行报告：A/B + 封闭 10 题——检索在 8 表小 Schema 上被预注册判据否决（2026-09-17）

> 授权链：①构建授权（零付费）→ 40 题人工审核 → ②付费门（cap 5 元）→ 用户「授权产品评测（cap 5 元）」→ A/B×40 → 胜者裁决 → 封闭 10 题（A 配置一次）。
> 实验：`product-eval-20260917-v3`（A/B×40，$0.050227）+ `-closed`（A×10，$0.009269）；全程 agent $0.0595 ≈ 0.42 元（cap 5 元内）。

## 1. 构建产物（零付费阶段）

- **题集**（`c52bc67`，v2）：50 题（30 开发 + 10 回归 + 10 封闭）中文问题 + 参考 SQL + gold 表标注；参考 SQL 双验证 = 真实库执行（非空/行数 ≤500）+ **QueryEngine AST 策略合规**——评测面与产品执行器面一致。
- **检索路线**：§10.2 路线 2 BM25（`rank-bm25==0.2.2` 锁版；tokenizer `bm25-tokenizer-v1`）+ zh glossary 桥（17 条通用术语映射，两条件同享）+ 预注册门槛（top-5、相对分 ≥0.3）。
- **Harness**：A=全量 26 目录文档 / B=门槛化检索子集 → 冻结 `retail_sql_generate` 步 → `submit_sql_candidate` 解析（非 SELECT 拒绝）→ QueryEngine 执行 → 多重集行等值 + Gold table Recall@5 + token/费用/延迟。

## 2. A/B 结果（40 可见题）

| 条件 | 正确 | 可执行 | Recall@5 | tokens | agent $ | 平均延迟 |
|---|---|---|---|---|---|---|
| **A 全量 Schema** | 3/40（7.5%） | 7/40 | **1.000** | 170,696 | 0.0320 | 3.5s |
| **B 检索** | 2/40（5.0%） | 4/40 | 0.910 | **64,046（−62.5%）** | 0.0182 | 3.2s |

## 3. §10.2 预注册判据裁决

- 正确率提升 ≥5pp？**否**（B 5.0% vs A 7.5%）。
- Gold Recall@5 不降 且 上下文 token −20%？**部分**（token −62.5% 达标；recall 0.910 < 1.0 不达标）。
- **裁决：B 被否决，胜者 = A（全量 Schema + 指标定义）**——与规格预设的小 Schema 结论一致（"如实报告小 Schema 下全量 Schema 更有效"）。封闭 10 题按规则仅用 A 跑一次：1/10 正确，$0.0093。

## 4. 主导失败模式（如实测量）

- **策略合规缺口**：单步 harness 未挂产品的 SqlReasoner 修复环，模型默认 SQL 风格（to_char/CASE 等）触发函数白名单拒绝（A 23/40、B 25/40），另有 ~26% 未按 `submit_sql_candidate` 工具应答（返回文本）。可执行率 A 17.5% / B 10% 封顶了正确率。
- 这测量的是**单步生成配置**，不是产品全链路能力（全链路含修复环与多步调查）；两条件面对同一策略面，A/B 相对比较仍然成立。
- 改进杠杆（后续）：sql_generate 提示内嵌函数白名单摘要、接入 SqlReasoner 修复环、封闭题扩容。

## 5. 构建期发现的产品真缺陷（已修复 + 测试）

- **`_ast_policy` 函数白名单误杀布尔运算符**：本版 sqlglot 的 `And`/`Or` 子类化 `Func`，导致**任何含 AND/OR 的查询都不可执行**——真实产品缺陷（多条件过滤是基本运营查询）。修复：布尔运算符豁免 + 守卫测试（`test_allows_boolean_operators_in_filters`）。
- gold 参考核对路径与 builder 统一（psycopg 参考路径 vs agent 策略引擎双轨）；runner 接 Selector 循环（坑 59 复现）。
- 题集 v1 有 5 题涉及身份列裸投影（seller_id/customer_unique_id/order_id）——与产品隐私红线冲突，重设计为州/城维度聚合（题面 v2）。

## 6. 账目与现场

- agent 合计 **$0.059496 ≈ 0.42 元**（cap 5 元内）；无 BIRD 栈参与（产品轨直连产品库 + DeepSeek）；产品 PG 业务表零变化（只读执行）。
- 产物：`outputs/product-eval/product-eval-*.jsonl` 三份报告（gitignored）；题集/glossary/harness 已入库（`c52bc67`/`09871dc`）；策略修复与 runner 修正随本轮 commit。
