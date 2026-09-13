# 2026-09-13 执行日志：Task 13 主运行（Gate P Pilot）与六个活体缺陷修复

> 会话：2026-09-13（Asia/Shanghai）· 执行者：Claude Code（Opus 5 1M）
> 范围：Task 13 主运行四轮尝试（a/b/c/d）+ 两个付费诊断集 + 六个首次真实栈活体缺陷的红绿修复 + §16.4 账本聚合
> 全程零 GT 内容读取（GT 仅经官方 orchestrator 子进程消费）；未 push；commit 待用户逐项授权

## 0. 授权链（用户决策记录）

1. 会话开场「阅读HANDOFF.md，授权执行下一步」= Task 13 主运行授权（HANDOFF §0.4 要求的新会话明确授权）。
2. 三个前置项打包裁定：① commit 09-13 全部 6 项（Gate G）→ `4ff1195`；② .env 追加 15 个 orchestrator 变量（LLM_PROVIDER/PG_MINCONN/PG_MAXCONN 经官方 shared/config.py AST 提取，该文件属 freeze 允许清单）；③ spike 容器 `docker stop` 释放 6002。
3. 首轮 20/20 infrastructure_error 后，恢复重启被 auto-mode 分类器按坑 5 拦截 → 用户裁定「授权恢复重启（同一 experiment，§8.5.3 恢复语义）」。
4. b 实验 19 failed（init 500 空壳）后，用户裁定「现在运行」新 experiment b……（b 因 provider_user_id 缺陷全灭）→ 用户裁定「现在运行」新 experiment c；c 因我方熔断判断主动停止 → 用户裁定「打包授权」（诊断循环 ≤$0.05 + 转绿后立即以新 experiment id 重启 Pilot，30 元上限/熔断双条款不变）。
5. 用户收尾指示：「继续跑，跑完做账本聚合和收尾三件套」。

## 1. 主运行结果（experiment pilot-day5-20260913d，唯一有效 Pilot）

| 项 | 值 |
|---|---|
| experiment_id | `pilot-day5-20260913d`（purpose=pilot；config_hash = task-selection.json SHA-256 `b1889777…9017`） |
| 终态 | attempted=20，**18 succeeded + 1 failed + 1 unfinished(infrastructure_error)**，exit 0，stopped=False |
| rewards | 18 集全部 reward=0.0，phase1/phase2 全 False（合法评测结果：a-mode 预算耗尽强制提交、c-mode 澄清循环至 max_turns——数据本身，非故障） |
| wall clock | a-mode 37–145s（预算耗尽退出）；c-mode 549–584s（≈600s max-turn 顶格） |
| agent 侧成本 | **$0.211203192 ≈ 1.4932 元**（@7.07 指示性汇率；600 个 spool 文件 / 675 模型轮；prompt 1,747,080 / completion 253,547（含 reasoning 184,852）/ total 2,000,627 tokens） |
| Gate P 上限 | 30 元——agent 侧已用 ~5%；simulator 侧不经过 spool，**余额交叉核对由用户执行（权威）** |
| failed 集 | `crypto_exchange_9 [c]`：episode 中段 run_session 503（服务瞬断；官方侧判 task error；契约：failed 有效不重跑） |
| unfinished 集 | `cybermarket_pattern_12 [a]`：official_process_failed（拆分文件正常；历轮同任务复现，判瞬态服务错误；可按 §8.5.3 同 experiment 新 attempt 恢复） |

§16.4 判据现状：`spent_so_far 1.4932 元 + Full 剩余 bootstrap 95% 上界（待按本轮实测单价外推）+ 产品未完成实验上界 ≤ 180 元`——Full 启动另行批准。

## 2. 六个活体缺陷（全部「离线全绿、首次真实栈暴露」——坑 2 家族）

| # | 缺陷 | 修复（红绿） |
|---|---|---|
| 1 | agent 镜像 requirements 只冻结了 uvicorn 链；首起缺 pydantic 等 5 包；裸 psycopg 无 pq wrapper | 重冻结 45 包（输入=镜像代码六顶层第三方导入 @产品 venv 已测版本；psycopg[binary]） |
| 2 | user-sim 镜像缺官方代码依赖 sqlglot；浮动范围重build漂移风险 | 按现有镜像 pip freeze 全钉 + sqlglot==30.17.0 |
| 3 | 评审格式快照（含 evidence 元数据）从未通过网关模型——Day 5 runtime 用窄基类直载即炸 | `CapabilityConfig/PriceConfig/TokenizerManifest` 上移至 `src/commerce_agent/model/snapshots.py` 为规范加载缝；工具改导入再导出；runtime 经加载缝 + tokenizer marker 校验 + archive 子目录；回归测试钉真实入库文件 |
| 4 | 冻结契约 fixture 未入镜像——每会话 init 必 500 | Dockerfile COPY fixture + .dockerignore 具体文件负向 + 结构守卫同步（COPY 集断言） |
| 5 | `DEEPSEEK_PROVIDER_USER_ID` 默认值非 32-hex——首模型轮深处 ValidationError→400 | runtime 默认 `"0"*32` + 工厂构造期 fail-fast 正则校验 + compose 默认同步 |
| 6 | ①bird_a profile 仍持 Day 3 合成工具目录（`synthetic_bird_a_*`）→ 首次工具调用 `action_not_allowlisted`；②预算门拒绝结果用固定 `call_id="budget_gate"` → ToolExchangeGroup id 失配 | ①manifest bird_a 换官方 9 工具 + run-profiles 规则同步 + 新守卫测试（BIRD 目录 ⊆ 官方 actions）；loader 改按 profile 查重、禁词表移除 "knowledge"（官方动作合法词汇）并留注释；②`_text_result(call, …)` 保留原调用 id/name（对齐官方 before_tool_callback 语义），RED 断言补进既有预算门测试 |

另：server 边界加**消毒日志**（ValidationError loc/type/msg 静态文本；500 全 traceback 到容器日志；payload 值零落地）——三次盲 400 教训的永久性修复；监控脚本两处自伤（glob 字符串误用、全窗口成本混入）修正。

## 3. 四轮尝试史（证据链）

| 轮 | experiment | 结果 | 根因 |
|---|---|---|---|
| 1 | `pilot-day5-20260913` | 20/20 infrastructure_error（official_process_failed），$0 | 宿主 venv 缺 dotenv/pydantic_settings → orchestrator import 即死 |
| 2（恢复 seq=2） | 同上 | 19 failed（init 500 空壳）+ 1 unfinished，$0 | fixture 不入镜像（缺陷④） |
| 3 | `pilot-day5-20260913b` | 19 failed（DTO 400 空壳）+ 1 unfinished，$0 | provider_user_id 非 hex（缺陷⑤） |
| 4 | `pilot-day5-20260913c` | 3 attempts 后我方主动停止（2 running 被掐、1 failed），$0.008 | 目录缺陷⑥①现形 |
| 诊断 | —（直跑 orchestrator，不写 eval 表） | c 集完整跑通（550.7s）；a 集两轮复现 400 → 锁定⑥①② | — |
| **5** | **`pilot-day5-20260913d`** | **见 §1** | 全部缺陷修复后 |

## 4. 终态验证证据（2026-09-13）

| 检查 | 结果 |
|---|---|
| 离线 suite | **829 passed, 128 skipped, 1 warning**（818 → 829：快照缝 4 + 工厂 2 + 边界日志 3 + 目录守卫 2） |
| Ruff（src tests scripts db/migrations bird_system_agent） | All checks passed |
| 契约测试 | contract 40+2 全绿（GT 隔离 9 条含新 COPY 集/负向断言） |
| 活体验证 | c-mode 550.7s 完整流 ✓；a-mode 135.3s 完整流（Budget used 18.0 coins）✓；d 实验 18 集端到端 ✓ |
| eval 表 | 11 张 Day 4 表未动；eval 四表仅 experiment/attempts/results（d 实验身份数据） |
| spike 容器 | stopped（6002 释放）；官方 db/product db 未动 |

## 5. 明确未做 / 遗留

- 未 push；本会话改动未 commit（清单见 HANDOFF §8，待用户逐项授权）。
- `crypto_exchange_9` failed 与 `cybermarket_pattern_12` unfinished 不重跑；后者可在下次授权下按 §8.5.3 同 experiment 恢复。
- 每集成本与 spool 轮次的精确按集归属（spool 导入接线）留 Day 6（run_scope_digest 传输方案未定）。
- rewards 全 0 的能力解读与 Full 启动判据外推 = Pilot 报告期工作（§16.4），不在本日志断言。
- simulator 侧用量 invisible——余额交叉核对由用户执行。

## 6. 新增坑（HANDOFF §6.8 同步）

54. **build 成功 ≠ 镜像可运行；服务起动 ≠ 服务可用；起动可用 ≠ 首轮可用。** 六个缺陷分布在这三层的每一层。凡「首次真实 X」都要预置可观测性（边界日志先行），否则每个缺陷多烧一轮授权。
55. **严格 DTO 加载共享配置目录是错的形状**：评审格式文件与运行时窄模型是两个合法层——上移 Config 类作规范缝，而不是放松运行时模型或手剥字段。
56. **合成替身/合成目录的命名假设会活体爆炸**：模型看到的工具目录必须与端口允许名单同源校验（目录 ⊆ 冻结契约 actions 的守卫），不能靠离线 fixture 巧合重名。
57. **拒绝/降级路径必须携带原调用身份**（call_id/name）：官方 before_tool_callback 语义是"替换该调用的结果"，不是"另发一条无主结果"。
58. **Windows 下 TaskStop/杀父进程不保证杀尽 detached 子进程**；付费运行的止血验证 = 双时点快照对比（文件计数/成本），不能凭停止成功回执就当已止血。
