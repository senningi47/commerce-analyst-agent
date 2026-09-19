# CommerceAnalyst — BIRD-Interact 电商经营分析与受控运营协同 Agent

以 Olist 2016–2018 匿名历史电商数据构建的电商经营分析 Agent：在 BIRD-Interact Full 600 题基准上完成可复现评测，同时实现「分析 → 提议 → 独立审批 → 受控执行 → 读回」的完整受控运营闭环。

## 架构

双轨隔离设计（规格 v0.3）：

- **Product 轨（受控运营闭环）**：`RetailGraph`（只读分析、只能提议）→ `OperationWorkflow`（propose/decide/execute 状态机，HMAC 授权、一次性 nonce、申请与审批人分离）→ `QueryEngine`（只读 SQL + EXPLAIN 防护 + AST 策略）→ `ops_read` 安全屏障视图。RetailGraph 永远拿不到执行能力；执行与读回全链审计。
- **BIRD 评测轨**：`EvaluationRunner`（任务级恢复、并发上限）驱动官方 ADK 编排器子进程，经 HTTP 对接 system-agent 容器、user-simulator 与数据库环境；agent 侧 spool JSONL 承载逐轮用量/费用遥测（fail-closed 导入）。
- **模型网关**：能力快照驱动的 DeepSeek 网关——请求绑定 run-scope/attempt/sequence、费用预估、fail-closed 响应校验、协议诊断插桩。
- **知识层**：知识目录（表/列/连接/指标/数据质量/权限 26 文档）+ BusinessValueResolver 别名解析 + BM25 检索路线（可证伪阶梯，见下）。
- **Web 工作台**：Vite + React；演示/实时双模式（SSE + Last-Event-ID 重连）、审批页、评测中心（真实评测数据）。

## 评测结果（2026-09-17 收官）

| 评测 | 规模 | 结果 |
|---|---|---|
| BIRD A4 Full（c 300 任务 + a 225 任务） | 530 attempts | 523 succeeded、6 failed、1 infra（重试后 300 个 c 任务 + 223 个 a 任务完成）；**有效评分 523 条 reward>0 = 0** |
| 消融 §17.2（修复 vs 停止，120 集） | 30 题 × c/a × A/B | 120/120 succeeded；**修复救回 0 集**（修复多耗费用 ~2.4–2.7×、轮次 ~1.37×） |
| 产品评测 §17.1（全量 Schema vs BM25 检索） | 40 题 A/B + 封闭 10 | **预注册判据否决检索路线，全量 Schema 胜出**（正确率 12.5% vs 10.0%）；检索省总 token 62.5% 但 Recall@5 降至 0.91 |

- **能力门未达的机制定位**（诚实结论）：领域知识缺口——知识查询 miss/定义不全时 agent 自造公式、user-simulator 拒答，提交即失败；修复价值经消融实证为零。改进杠杆（v4 提示策略、修复环）已验证「行为改善、reward 中性」，归档为将来工作。
- **预算纪律**：全程 160 元预算线，**平台实扣终账 91.06 元（57%，2026-09-19 对账，含 sim 侧）**；收官混合估算 88.19 与实差 +2.87（≈3.3%，sim + 汇率漂移）留档；BIRD 轨 peak 档（2× 单价）零泄漏（平台账单旁证）；三次安全暂停/截停裁定全部留痕。
- 过程中发现并修复产品真缺陷：查询策略函数白名单误杀布尔运算符（任何含 AND/OR 的查询不可执行）、c 模式跨相位会话失忆、推理输出水位线（8192/16384 → 官方等效 64K）等，详见 `docs/reports/`。

## 仓库结构

```
src/commerce_agent/       产品与评测主包
  orchestration/            RetailGraph、BIRD 轨 adapter/图
  operations/               受控运营状态机（HMAC grant、幂等）
  query_engine/             只读 SQL 执行器（AST 策略、EXPLAIN 阈值、对账）
  knowledge/ value_resolver/  知识目录、别名解析、BM25 检索
  model/                    DeepSeek 网关、能力快照、turn store
  context_builder/          配置驱动上下文（canonical JSON、裁剪、预算）
  evaluation/               EvaluationRunner、评测契约、spool 导入
  product_eval/             Day 4 确定性场景 + §17.1 开放题评测
  api/                      FastAPI 事件面（SSE、只读 eval/runs 视图）
bird_system_agent/        BIRD system-agent 服务（容器入口）
web/                      工作台前端（Vite + React + ECharts）
db/migrations/            PostgreSQL 迁移（0001–0007，含安全屏障视图）
tests/                    unit / contract / integration / e2e 四层
configs/model/            冻结的模型能力/价格/策略/运行 profile 快照
scripts/                  评测 preflight、题集构建、spool 导入、探针
docs/                     规格、计划、执行日志、评测报告（全留痕）
```

## 快速开始

```bash
# 一次性准备（免费，仅 CDN 下载并 fail-closed 校验 tokenizer 工件）
uv run python scripts/provision_deepseek_tokenizer.py install --manifest configs/model/deepseek-tokenizer-artifact.v1.json

# 离线测试（无数据库/无 API key）
uv run pytest -q                       # 933 passed, 140 skipped
uv run ruff check src tests scripts db/migrations

# PostgreSQL 集成/e2e（需 docker compose 起产品库 + .env）
uv run --env-file .env pytest tests/integration tests/e2e -m postgres -q

# 产品评测（付费门：需授权与预算）
uv run --env-file .env python scripts/build_product_question_bank.py   # 题集构建+验证
uv run --env-file .env python scripts/run_product_eval.py --condition ab \
  --experiment <id> --sets development regression
```

BIRD 评测轨的完整启动程序（preflight → compose 栈 → runner）见 `docs/superpowers/plans/` 与 `scripts/preflight_bird_stack.py`。

## 文档索引

- 权威规格：`docs/project/specs/BIRD-Interact电商经营分析Agent-设计规格-v0.3.md`
- 最终报告（全部评测与决策）：`docs/reports/2026-09-17-final-report.md`
- 执行日志（34 节，逐轮留痕）：`docs/reports/2026-09-14-day6-phase-a-execution-log.md`
- 面试叙事材料：`docs/interview-prep.md`
