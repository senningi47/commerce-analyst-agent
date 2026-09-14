/**
 * 评测中心（只读）。默认对接只读 eval API（migration 0007 视图 +
 * agent_reader 身份）；API 不可达时回退演示快照并明确标注。
 * rewards 全 0 的 Pilot 数据如实展示（§20.3 收窄面：运行、c/a、P1/P2、费用、轮次）。
 */
import { useEffect, useState } from "react";
import {
  fetchEvalAttempts,
  fetchEvalExperiments,
  type EvalAttemptRecord,
  type EvalExperimentSummary,
} from "../api";

const STATUS_LABELS: Record<string, string> = {
  pending: "排队",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  infrastructure_error: "基础设施错误",
  interrupted: "中断",
};

const DEMO_EXPERIMENT: EvalExperimentSummary = {
  experiment_id: "pilot-day5-20260913d",
  purpose: "pilot",
  config_hash: "b1889777",
  created_at: "2026-09-13T08:00:00Z",
  closed_at: null,
  attempt_total: 20,
  status_counts: { succeeded: 18, failed: 1, interrupted: 1 },
  reward_total: "0.0",
  agent_cost_total: "0.211203192",
};

function statusCount(
  summary: EvalExperimentSummary | null,
  key: string,
): number {
  return summary?.status_counts[key] ?? 0;
}

function usd(amount: string | null): string {
  if (amount === null) return "—";
  const value = Number(amount);
  return `$${Number.isNaN(value) ? amount : value.toFixed(4)}`;
}

export function EvalCenterPage() {
  const [experiments, setExperiments] = useState<EvalExperimentSummary[] | null>(
    null,
  );
  const [attempts, setAttempts] = useState<EvalAttemptRecord[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchEvalExperiments()
      .then(async (list) => {
        if (cancelled) return;
        setExperiments(list);
        setUnavailable(false);
        if (list.length > 0) {
          try {
            const rows = await fetchEvalAttempts(list[0].experiment_id);
            if (!cancelled) setAttempts(rows);
          } catch {
            if (!cancelled) setAttempts(null);
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setExperiments(null);
          setAttempts(null);
          setUnavailable(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const live = experiments !== null && experiments.length > 0;
  const summary = live ? experiments![0] : DEMO_EXPERIMENT;
  const succeeded = statusCount(summary, "succeeded");
  const failed = statusCount(summary, "failed");
  const infrastructure = statusCount(summary, "infrastructure_error");
  const interrupted = statusCount(summary, "interrupted");
  const pendingOrRunning =
    statusCount(summary, "pending") + statusCount(summary, "running");
  const completionDelta = [
    `${failed} failed`,
    ...(infrastructure > 0 ? [`${infrastructure} 基础设施错误`] : []),
    ...(interrupted > 0 ? [`${interrupted} 中断`] : []),
    ...(pendingOrRunning > 0 ? [`${pendingOrRunning} 进行中`] : []),
  ].join(" · ");
  const telemetryBackfilled = (attempts ?? []).some(
    (row) => row.agent_cost_amount !== null,
  );

  return (
    <div className="evalcenter">
      {!live && (
        <div className="stage-note">
          {unavailable
            ? "评测 API 未连接——以下为演示快照（Pilot 真实口径）。实时模式需启动 API（create_postgres_app）。"
            : "评测数据加载中…"}
        </div>
      )}
      <div className="metrics four">
        <div className="card metric tone-neutral">
          <div className="card-label">评测集</div>
          <div className="metric-value mono">{summary.attempt_total}</div>
          <div className="metric-delta">
            c ×{attempts ? attempts.filter((row) => row.mode === "c").length : "—"} ·
            a ×{attempts ? attempts.filter((row) => row.mode === "a").length : "—"}
          </div>
        </div>
        <div className="card metric tone-neutral">
          <div className="card-label">有效完成</div>
          <div className="metric-value mono">{succeeded}</div>
          <div className="metric-delta">{completionDelta}</div>
        </div>
        <div className="card metric tone-down">
          <div className="card-label">reward 合计</div>
          <div className="metric-value mono">{summary.reward_total}</div>
          <div className="metric-delta">P1/P2 通过数见明细 · 策略层问题，通道无缺陷</div>
        </div>
        <div className="card metric tone-neutral">
          <div className="card-label">agent 侧费用（USD）</div>
          <div className="metric-value mono">{usd(summary.agent_cost_total)}</div>
          <div className="metric-delta">
            {telemetryBackfilled
              ? `${summary.experiment_id} · ${summary.purpose}`
              : "spool 未回填（旧格式）· 实际消耗见 Pilot 账本"}
          </div>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <div className="card-label">运行明细 · {summary.experiment_id}</div>
          <div className="card-src mono">ops_read.eval_attempts（只读视图）</div>
        </div>
        {attempts === null ? (
          <div className="muted">
            {live
              ? "该实验暂无 attempt 明细（或明细 API 未连接）。"
              : "演示快照不含逐集明细——连接评测 API 后展示真实 20 集。"}
          </div>
        ) : (
          <table className="result">
            <thead>
              <tr>
                <th>题目</th>
                <th>c/a</th>
                <th>#</th>
                <th>状态</th>
                <th>P1</th>
                <th>P2</th>
                <th>reward</th>
                <th>轮次</th>
                <th>提交</th>
                <th>agent 费用</th>
                <th>sim 费用</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              {attempts.map((row) => (
                <tr key={row.attempt_id}>
                  <td className="mono">{row.task_id}</td>
                  <td className="mono">{row.mode}</td>
                  <td className="mono">{row.attempt_seq}</td>
                  <td>{STATUS_LABELS[row.status] ?? row.status}</td>
                  <td className="mono">{row.phase1_passed === null ? "—" : row.phase1_passed ? "✓" : "✗"}</td>
                  <td className="mono">{row.phase2_passed === null ? "—" : row.phase2_passed ? "✓" : "✗"}</td>
                  <td className="mono zero">{row.reward ?? "—"}</td>
                  <td className="mono">{row.rounds ?? row.agent_turns ?? "—"}</td>
                  <td className="mono">{row.submit_count ?? "—"}</td>
                  <td className="mono">{usd(row.agent_cost_amount)}</td>
                  <td className="mono">{usd(row.simulator_cost_amount)}</td>
                  <td className="mono dim">{row.error_class ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="stage-note">
          两门基线：预算门 FAIL（457.6 元 / 160 元线）· 能力门 FAIL（rewards 全
          0）。策略修复（prompt-policies v3）后以 Task 10 小样本实测更新本页。
        </div>
      </section>
    </div>
  );
}
