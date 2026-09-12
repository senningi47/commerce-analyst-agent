# Gate P 授权请求：20 题真实 BIRD Pilot

> 状态：**待用户审批** —— 本文件本身不含任何已执行的付费动作。
> 日期：2026-09-12（Asia/Shanghai） · 依据：v0.3 §15/§16、Day 5 计划 Task 12/13
> 产出脚本：`scripts/prepare_bird_pilot.py`（全程零 API 请求）

## 1. 请求概要

请求用户授权执行 **Task 13：20 题真实 Pilot**（c-Interact ×10 + a-Interact ×10，5 数据库分层），使用 DeepSeek 官方 `deepseek-v4-flash`，经 `EvaluationRunner`（Semaphore 2，任务级恢复）驱动官方 orchestrator 子进程完成。

## 2. 全新运行身份（不复用 Day 3 v4 身份 —— HANDOFF 坑 36）

| 项 | 值 |
|---|---|
| experiment_id | `pilot-day5-<执行日YYYYMMDD>`（执行时确定并写入 eval.experiment） |
| purpose | `pilot` |
| run_id / attempt_id | 每次执行由 Runner 生成（UUID4，随事件日志持久化） |
| artifact 身份 | `outputs/bird-pilot/task-selection.json` + `outputs/bird-eval/events-<experiment_id>.jsonl` |
| 执行日志 | `docs/reports/` 下新执行日志（收尾三件套） |

## 3. 成本上限（建议值，**须由用户最终裁定**）

- **建议上限：30 元（人民币）**，占 200 元总预算的 15%。
- 依据：Pilot 的目的是为 §16.4 账本提供实测单价（每 episode 的 prompt/cache-hit/cache-miss/completion/reasoning token 与轮次），20 集 × 单集上界（thinking 8192 输出 + 64K 上下文 + Simulator 2048）在官方价目（缓存未命中输入 1 元/M、输出 2 元/M）下不足以精确预估，30 元上限 + 每集遥测 + **中途熔断条款**（下条）构成安全边界。
- **熔断条款**：实测账本投影（`spent_so_far + 剩余 bootstrap 95% 上界`）超过 **180 元线**，或单集成本超出 Pilot 首五集中位数的 4 倍时，Runner 安全暂停，**剩余 episode 不再执行**，回到用户决策。

## 4. 探针与快照要求（Pilot 开始前必须完成，人工记录）

1. DeepSeek 能力探针重跑（新 probe 身份），保存实际模型名/Thinking/Tool Calling/usage 字段 → 更新 capability snapshot；
2. 官方价格页快照 + 账户余额截图（前后各一次，与 usage 交叉核对）；
3. `scripts/prepare_bird_pilot.py --run-db-check`：官方 metadata checker 复跑（22 库/244 表/2,011 列/273,571 行基线）；
4. GT 路径拒绝检查：system agent 容器内读取 GT env 指向路径必须失败（容器已起时执行）；
5. `evaluation_writer` 角色 provisioning + migration `0005` 应用（**显式授权的 DB 激活检查点**）+ live 契约测试（`-m postgres`）转绿；
6. Dockerfile base image digest 固化 + `docker compose -f compose.bird.yaml build` 验证 + requirements 冻结。

## 5. 重跑条款

- **一次正向运行 = 一次授权额度。** 任何原因导致的 Pilot 中止/失败，续跑或重跑都需要**重新授权**；
- `infrastructure_error` / `interrupted` 的任务按 §8.5.3 由 Runner 在**同一次授权内**自动以新 attempt 重跑（这是恢复语义，不是重跑）；
- 任务级评测失败（`failed`）是有效结果，**不得**以任何名义重跑；
- 结果不可覆盖：修复后使用新 experiment_id。

## 6. Full 启动判据（Phase C，另行决策）

Pilot 完成后按 §16.4 账本评估：`已实际花费 + Full 剩余 1200-20 集费用 bootstrap 95% 上界 + 产品 50 题与实验费用上界 ≤ 180 元`；每 25 个 Full 任务重估。Full 启动本身需要用户在 Pilot 报告之上的**另行批准**。

## 7. 当前状态清单（截至本请求）

| 项 | 状态 |
|---|---|
| 契约冻结 / adapter / HTTP 端口 / Runner | ✅ 已实施（Day 5 Task 1–11，全离线证据） |
| 20 题选取 | ⏳ 待 `--dataset` 执行（写 per-task 拆分 + 公开选取清单） |
| 0005 激活 + 角色 provisioning | ⏳ 待显式授权（见 §4.5） |
| 探针 / 价格快照 / 余额核对 | ⏳ 待执行（§4.1–4.2） |
| 付费 API 请求 | ❌ **零**（Gate P 关闭中） |
