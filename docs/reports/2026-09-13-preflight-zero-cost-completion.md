# 2026-09-13 执行日志：Gate P preflight 零付费项收尾（选取 + db-check + GT 拒绝检查）

> 会话：2026-09-13（Asia/Shanghai）· 执行者：Claude Code（Opus 5 1M）
> 范围：HANDOFF §5.1 剩余三项零付费 preflight 全部完成；途中红绿修复 `prepare_bird_pilot.py` 三个缺陷
> 全程零付费 API 请求、零 GT 内容读取、未 push、未 commit（Git 授权待用户逐项给出）

## 0. 授权记录

新会话开场用户指示「**继续 preflight 零付费项**」。Gate P 的 preflight+Pilot 打包授权为 2026-09-12 会话所给；按跨会话授权不继承规则，本会话的三项零付费操作由上述指示重新激活。**Task 13 主运行（付费）仍需另行明确授权。**

## 1. 执行结果总览（三项全 PASS）

| 项 | 结果 | 证据 |
|---|---|---|
| revisions 核验（脚本自带） | PASS | `source_revision 451fe2c…` MATCH；镜像 digest `de4b88f6…` MATCH |
| ① 20 题选取 | PASS | seed 7；c=10 / a=10；10 库覆盖；strategy=`database+ambiguity-fallback (v0.3 §16.3 fallback; no BI/DM labels assumed)` |
| ② 官方 db-check | PASS | `returncode 0`；**22 库 / 244 表 / 2,011 列 / 273,571 行**与 Day 1E 基线全一致（`all_match: true`） |
| ③ GT 路径拒绝检查 | PASS | 见 §4 |

产出物：
- `outputs/bird-pilot/task-selection.json` —— 公开选取清单（20 题，仅 task_id/mode/分层依据）
- `outputs/bird-pilot/task-data/*.jsonl` —— 20 个 per-task GT 拆分（**gitignored**，仅供官方 orchestrator 子进程消费）
- `outputs/bird-budget/pilot-ledger.json` —— §16.4 账本种子（测量字段留待 Pilot 实测填充）

20 题清单（公开字段）：

```text
c-mode (10): archeology_scan_M_1(amb5) cold_chain_pharma_compliance_3(2) cross_border_M_8(2)
             crypto_exchange_9(2) cybermarket_pattern_6(3) disaster_relief_3(4)
             exchange_traded_funds_M_3(4) fake_account_6(2) households_4(4) hulushows_19(5)
a-mode (10): archeology_scan_6(3) cold_chain_pharma_compliance_8(2) cross_border_M_5(5)
             crypto_exchange_11(2) cybermarket_pattern_12(3) disaster_relief_7(3)
             exchange_traded_funds_M_1(4) fake_account_7(2) households_M_2(4) hulushows_2(4)
```

## 2. 本轮发现并修复的三个脚本缺陷（`scripts/prepare_bird_pilot.py` + 其单测）

首跑 db-check 失败（returncode 2、解析全 null），按纪律停止 → 最小化诊断 → 逐层剥出三个缺陷（前一个掩盖后一个）：

1. **checker 子进程极简 env 破坏 Windows 临时目录解析**。原代码只传 `PYTHONIOENCODING` + `PATH`；TEMP/TMP 缺失使 uv 把临时目录解析到 `C:\WINDOWS\` → `os error 5`，checker 根本没执行。
   修复：新增 `_checker_env()` **运行时变量白名单**（PATH、TEMP、TMP、USERPROFILE、HOME、HOMEDRIVE、HOMEPATH、LOCALAPPDATA、APPDATA、SYSTEMROOT、SYSTEMDRIVE、UV_CACHE_DIR、XDG_CACHE_HOME）——既修复 uv/Python 运行时，又保持原意图：**秘密（如 DEEPSEEK_API_KEY）不进官方 checker 子进程**（单测以 monkeypatch 注入假密钥断言被剔除）。
2. **默认 `--adk-root` 指向不存在的路径**。默认拼 `BIRD-Interact-ADK/env/check_db_metadata.py`，实际 checker 在 `_upstream/BIRD-Interact/env/check_db_metadata.py`（`BIRD-Interact-ADK/` 子目录无 `env/`）。修复：`DEFAULT_ADK_ROOT = Path("_upstream/BIRD-Interact")`，单测钉死拼路径。
3. **基线解析正则假阳性**。`databases?\D{0,20}?(\d…)` 命中输出首行 `Checking database metadata for 127.0.0.1:5433` → 解析出 databases=127（实为 IP），假阴性漂移判定；tables/columns/rows 恰好落在正确行（碰巧）。修复：四个正则全部锚定 checker 真实汇总行（`Total Databases/Tables/Columns/Rows:`，大小写不敏感），注释说明误命中机理；**单测合成输出替换为真实 checker 输出样本**（原合成格式 `Expected databases: 22` 等与真实输出不符），并新增「头部 IP 不得误命中」回归测试。

TDD 证据链：RED（`ImportError: cannot import name 'DEFAULT_ADK_ROOT'`；`assert 127 != 127`）→ GREEN → owning tests `11 passed` → broader `tests/unit/scripts` **157 passed** → 全量离线 **818 passed, 128 skipped, 1 warning**（815 → 818：新增 2 + 解析组 2 改 3，净 +3）→ Ruff `src tests scripts db/migrations` 全绿。

## 3. GT 纪律执行情况

- **零 GT 内容读取**：容器内检查只探测可读性（`open("rb")` 成功即闭、失败返回异常类型名，**零字节读取**）；宿主侧仅做 GT 目录存在性检查（`True`，使容器内失败成为有对照的证明）。
- **公开字段校验**：`task-selection.json` 全文对 `sol_sql` / `test_cases` / `evaluator_only` 三 token 零命中。
- **GT 拆分仅落 gitignored 目录**：脚本内建 gitignore 守卫（`_gitignore_covers`）在写第一个字节前强制；20 个文件全部在 `outputs/bird-pilot/task-data/`。

## 4. GT 拒绝检查明细（规格 §19.3 / 授权请求 §4.4 运行时对等物）

执行方式：`docker compose --env-file .env -f compose.bird.yaml run --rm --no-deps bird-system-agent python -c <check>` —— 与服务定义同镜像/同 env/同唯一 spool 卷的一次性容器；`--no-deps` 不牵动依赖服务。

```text
gt_relative_exists=False; gt_relative_open=FileNotFoundError      # data/raw/bird-interact-full/evaluator_only
dbenv_public_data_exists=False; dbenv_public_data_open=FileNotFoundError  # /opt/bird-adk/bird-interact-full
gt_or_forbidden_env_names=[]                                      # 零 GT/PG/LITELLM/USER_SIM env 名
container_exit=0（一次性容器已 --rm 自清理）
```

结构前提由契约测试 `tests/contract/test_bird_gt_isolation.py`（9 条断言）锁定；本检查是其在真实容器内的实测对等物。

## 5. 环境现场与遗留

- `bird_interact_postgresql_full` 已连跑 13 天（5433），元数据与 Day 1E 基线零漂移。
- **`commerce_analyst_bird_db_spike`（Day 1 遗留 spike）占 127.0.0.1:6002**，与 compose 拓扑 `bird-db-environment` 的端口冲突——**Task 13 起真实 compose 栈前必须处置**（停用/移除该容器）。本轮未动它。
- compose 网络 `commerce_analyst_bird_eval` 已创建；orphan 警告（product postgres 属另一 compose 项目）为预期现象。
- 官方 checker 直跑需 `PYTHONIOENCODING=utf-8`（emoji 输出在 GBK 控制台会 `UnicodeEncodeError`）——脚本子进程已固化该设置。

## 6. 新增坑（HANDOFF §6.7 同步）

52. **离线单测的合成输出若从未与真实输出对照，会同时掩盖「解析缺陷」与「合成格式错误」双缺陷。** 本次 db-check 解析正则假阳性 + 单测合成格式失真叠加，只有 live 实跑才暴露——坑 2（默认 skip 不是证据）/坑 50（以真实调用面为准）的又一变体：**凡解析外部工具输出的代码，其测试样本必须来自该工具的一手真实输出**。
53. **Windows 下给子进程传白名单 env 必须包含 TEMP/TMP/USERPROFILE/LOCALAPPDATA/SYSTEMROOT 等运行时变量**，否则 uv/Python 把临时目录解析到 `C:\WINDOWS\`（os error 5），错误信息完全不指向真实根因。最小 env 白名单要按「运行时需要什么」而非「想隐藏什么」来设计，并用假密钥注入测试锁定剔除语义。

## 7. 明确未做

Task 13 主运行（付费，待授权）；spike 容器 6002 处置；commit/push（改动文件见 HANDOFF §8，待用户逐项授权）；Day 6/7 规划；spool 导入接线；探针重设计。评估栈未起（GT 检查用一次性容器，无需常驻服务）。
