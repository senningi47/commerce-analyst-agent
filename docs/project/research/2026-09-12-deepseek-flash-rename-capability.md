# DeepSeek 模型更名与 V4.1-Flash 能力核验（Day 5 preflight ④）

> 日期：2026-09-12 · 执行者：Claude Code（Opus 5 1M）· Gate P preflight ④ 证据文档
> 结论：`deepseek-v4-flash` 已于 2026-09-10 随 **DeepSeek-V4.1-Flash** 发布退役；规范请求名改为
> **`deepseek-flash`**；响应 `model` 回显为 `deepseek-flash`。能力快照 v4 与价格快照 v4 据此重建。

## 1. 发现过程（两次探针失败 → 免费侦查 → 形状诊断）

1. 2026-09-12 两次付费探针（`day5-retail-probe-v5`，证据文件
   `outputs/probes/day5-retail-probe.redacted.json` 与
   `outputs/probes/day5-retail-probe-20260912-v5.redacted.json`，合计成本 ~$0.001）均被网关
   fail-closed 拒绝：`provider_response_invalid`（usage 解析成功、`_parse_output` 失败）。
2. 免费侦查：`GET https://api.deepseek.com/models`（200）仅列出 **`deepseek-flash`** 与
   **`deepseek-v4-pro`**，`deepseek-v4-flash` 不再出现。
3. 官方文档（首页模型表）确认：`deepseek-v4-flash` 与 `deepseek-v4-flash-vision-exp` 为遗留名，
   仍被接受，但对应模型**已退役**；请求由 **DeepSeek-V4.1-Flash** 服务并按 Flash 价格计费。
   因此：请求成功、计费正常，但响应回显改用规范名 → 我方网关
   `payload["model"] != capability.response_model` 校验失败。**网关与 graph 的 fail-closed 行为完全正确。**

## 2. 形状诊断（一次最小付费调用，~$0.00001）

请求：`model=deepseek-flash`，`reasoning_effort=none`，`max_tokens=8`，`user_id=<32×0>`。
响应（仅结构字段）：200；顶层键 `choices/created/id/model/object/system_fingerprint/usage`；
`model=deepseek-flash`；无 `model_version` 字段；`finish_reason=stop`；
message 键 `content/role`；usage 含 `prompt_tokens/prompt_cache_hit_tokens/prompt_cache_miss_tokens/
completion_tokens/total_tokens` + `prompt_tokens_details`（新）；thinking 开启时
`completion_tokens_details.reasoning_tokens` 存在（由第二次探针实测确认）。

## 3. 官方文档证据（2026-09-12 抓取，哈希见 candidate 文件）

抓取脚本：`scripts/snapshot_deepseek_model_config.py fetch-candidate` →
`outputs/probes/deepseek-config-20260912-candidate.json`。要点：

- 模型枚举：`model ∈ {deepseek-flash, deepseek-v4-pro}`；
- `reasoning_effort ∈ {none, low, high, max}`，**默认 effort 为 high**（兼容映射 minimal→low、medium/xhigh→high）；
  另有新 `thinking` 对象（enabled/disabled，默认 enabled）——我方继续发送 `reasoning_effort`，无需改请求构造；
- `max_tokens` 上限 **384K（393216）**；非思考默认 8K、思考默认 64K（max effort 128K）；
- Models & Pricing 页：`deepseek-flash` 的 `MODEL VERSION = DeepSeek-V4.1-Flash`、
  `CONTEXT LENGTH = 1M`、`MAX OUTPUT MAXIMUM = 384K`、`Tool Calls ✓`、思考/非思考双模式（默认思考）；
- 行为漂移提醒：同一探针 prompt 的 reasoning tokens 从 09-06 的 112 涨到 09-12 的 370–491
  （high 档思考变重）→ Pilot 单集思考成本预估需上浮，30 元上限 + 熔断条款覆盖。

## 4. 对 v0.3 的含义

- 我方网关是**快照驱动**的：`requested_model/response_model` 均来自 capability snapshot，网关代码零改动；
- 配置面更新（capability v4 + price v4 + probe gate + runtime/compose 默认 `SYSTEM_AGENT_MODEL`
  + profiles `capability_revision`）由本日执行日志记录的 red-green 承载；
- 价格数字由用户人工核对价格页后填入 price snapshot v4（§4.2 要求的人工动作，余额截图同批）。
