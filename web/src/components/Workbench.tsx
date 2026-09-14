import type { LoopStage, RunEvent } from "../types";
import type { ViewModel } from "../view";
import { STAGE_LABELS } from "../types";
import { ResultChart } from "./ResultChart";

const EMPTY_HINTS: Partial<Record<LoopStage, string>> = {
  empty: "发起一个经营问题后，事件流将驱动本工作台。",
  clarifying: "澄清中：Agent 正在确认业务口径。",
  sql_rejected: "SQL 校验被拒绝：已进入修复循环。",
  sql_repaired: "SQL 已修复并通过校验。",
  insufficient: "证据不足：无法在当前证据下得出结论。",
  awaiting_approval: "提案已创建，等待运营审批。",
  approval_rejected: "审批被拒绝：等待修订后重新提交。",
  interrupted: "运行被人为中断；断点已保存。",
  resumed: "运行已恢复：从断点继续。",
};

export function Workbench({
  stage,
  view,
  events,
}: {
  stage: LoopStage;
  view: ViewModel;
  events: RunEvent[];
}) {
  return (
    <div className="workbench">
      <section className="panel conversation">
        <h3>对话与结论</h3>
        {view.question && <div className="bubble user">{view.question}</div>}
        {view.clarifications.map((item, index) => (
          <div key={index} className="clarify">
            <div className="bubble agent">❓ {item.question}</div>
            <div className="bubble user reply">{item.answer}</div>
          </div>
        ))}
        {view.insufficient && (
          <div className="notice warn">证据不足——已按停止条件结束，可补充数据后重试。</div>
        )}
        {view.conclusion && !view.insufficient && (
          <div className="bubble agent conclusion">📌 {view.conclusion}</div>
        )}
        {EMPTY_HINTS[stage] && <div className="notice muted-note">{EMPTY_HINTS[stage]}</div>}
      </section>

      <section className="panel evidence">
        <h3>计划 · 证据 · SQL · 结果 · 对账</h3>
        {view.planSteps.length > 0 && (
          <ol className="plan">
            {view.planSteps.map((step) => (
              <li key={step.id} className={step.status}>
                {step.status === "done" ? "✅" : "⏳"} {step.title}
              </li>
            ))}
          </ol>
        )}
        {view.evidence.length > 0 && (
          <div className="evidence-row">
            {view.evidence.map((item, index) => (
              <span key={index} className="evidence-chip" title={item.digest}>
                {item.kind} · {item.ref}
              </span>
            ))}
          </div>
        )}
        {view.sqlVersions.map((item, index) => (
          <div key={index} className={item.rejected ? "sql rejected" : "sql ok"}>
            <div className="sql-label">
              SQL v{index + 1}
              {item.rejected ? " · 被拒绝" : " · 已通过校验"}
            </div>
            <pre>{item.sql}</pre>
            {item.rejected && <div className="reject-reason">↳ {item.rejected}</div>}
          </div>
        ))}
        {view.table && (
          <table className="result">
            <thead>
              <tr>
                {view.table.columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {view.table.rows.map((row, index) => (
                <tr key={index}>
                  {view.table!.columns.map((column) => (
                    <td key={column}>{String(row[column] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {view.chart.length > 0 && <ResultChart data={view.chart} />}
        {view.reconciliation.map((item, index) => (
          <div key={index} className={item.passed ? "recon ok" : "recon fail"}>
            {item.passed ? "✓" : "✗"} 对账 {item.rule}：{item.detail}
          </div>
        ))}
        {events.length === 0 && <div className="notice muted-note">等待事件…</div>}
      </section>

      <section className="panel timeline">
        <h3>事件时间轴（{STAGE_LABELS[stage]}）</h3>
        <ul>
          {events.map((item) => (
            <li key={item.cursor}>
              <span className="cursor">#{item.cursor}</span> {item.eventType}
              <span className="muted"> seq {item.sequence}</span>
            </li>
          ))}
          {events.length === 0 && <li className="muted">（无）</li>}
        </ul>
      </section>
    </div>
  );
}
