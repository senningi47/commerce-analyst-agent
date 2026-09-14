import { useState } from "react";
import type { LoopStage, RunEvent } from "../types";
import type { ViewModel } from "../view";
import { ResultChart } from "./ResultChart";

const STAGE_HINTS: Partial<Record<LoopStage, string>> = {
  empty: "在上方输入一个经营问题，或从建议问题开始。",
  clarifying: "Agent 正在与您确认业务口径，回答后继续分析。",
  sql_rejected: "第一版查询未通过校验，正在自动修复。",
  sql_repaired: "查询已修复并通过只读校验。",
  insufficient: "当前证据不足以支撑结论，已按停止条件结束。",
  awaiting_approval: "提案已创建，等待运营负责人审批。",
  approval_rejected: "审批人要求修订，请查看审批意见后重新提交。",
  interrupted: "运行被人为中断，断点已保存，可安全恢复。",
  resumed: "已从断点恢复运行。",
};

function toneClass(tone?: string): string {
  if (tone === "down") return "tone-down";
  if (tone === "up") return "tone-up";
  return "tone-neutral";
}

export function Workbench({
  stage,
  view,
  events,
}: {
  stage: LoopStage;
  view: ViewModel;
  events: RunEvent[];
}) {
  const [traceOpen, setTraceOpen] = useState(
    stage === "sql_rejected" || stage === "sql_repaired",
  );
  const [processOpen, setProcessOpen] = useState(
    stage === "clarifying" || stage === "insufficient",
  );

  return (
    <div className="workbench">
      <div className="main-col">
        {STAGE_HINTS[stage] && <div className="stage-note">{STAGE_HINTS[stage]}</div>}

        {view.conclusion && !view.insufficient && (
          <section className="card verdict">
            <div className="card-label">结论</div>
            <p className="verdict-text">{view.conclusion}</p>
            {view.executionRef && (
              <div className="receipt-line">
                执行读回 <span className="mono">{view.executionRef}</span> · 状态一致
              </div>
            )}
          </section>
        )}
        {view.insufficient && (
          <section className="card verdict warn">
            <div className="card-label">结论 · 证据不足</div>
            <p className="verdict-text">{view.conclusion}</p>
            <div className="hint-line">下一步：接入流量与转化漏斗数据后重新发起分析。</div>
          </section>
        )}

        {view.metrics.length > 0 && (
          <div className="metrics">
            {view.metrics.map((metric) => (
              <div key={metric.label} className={`card metric ${toneClass(metric.tone)}`}>
                <div className="card-label">{metric.label}</div>
                <div className="metric-value mono">{metric.value}</div>
                {metric.delta && <div className="metric-delta">{metric.delta}</div>}
              </div>
            ))}
          </div>
        )}

        {view.chart.length > 0 && (
          <section className="card">
            <div className="card-head">
              <div className="card-label">GMV 日趋势 · 本周 vs 上周</div>
              <div className="card-src mono">retail.orders × retail.customers</div>
            </div>
            <ResultChart data={view.chart} />
          </section>
        )}

        {view.proposal && (
          <section className="card proposal-strip">
            <div className="card-head">
              <div className="card-label">运营动作提案 · {view.proposal.meta?.title}</div>
              <a
                className="go-link"
                href="#approvals"
                onClick={(e) => {
                  e.preventDefault();
                  window.dispatchEvent(new CustomEvent("goto-approvals"));
                }}
              >
                去审批 →
              </a>
            </div>
            <div className="prop-meta">
              <span>{view.proposal.meta?.requester}</span>
              <span>{view.proposal.meta?.kind}</span>
              <span>预算影响 {view.proposal.meta?.budgetImpact}</span>
            </div>
          </section>
        )}

        <section className="card fold">
          <button className="fold-head" onClick={() => setProcessOpen(!processOpen)}>
            <span className="fold-mark">{processOpen ? "−" : "+"}</span>
            分析过程（澄清 {view.clarifications.length} · 计划步骤 {view.planSteps.length}）
          </button>
          {processOpen && (
            <div className="fold-body">
              {view.clarifications.map((item, index) => (
                <div key={index} className="qa">
                  <div className="qa-q">向您确认：{item.question}</div>
                  <div className="qa-a">您的回答：{item.answer}</div>
                </div>
              ))}
              {view.clarifications.length === 0 && (
                <div className="muted">本轮无需澄清。</div>
              )}
              <ol className="plan">
                {view.planSteps.map((step) => (
                  <li key={step.id}>
                    <span className={step.status === "done" ? "dot done" : "dot pending"} />
                    {step.title}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </section>

        <section className="card fold">
          <button className="fold-head" onClick={() => setTraceOpen(!traceOpen)}>
            <span className="fold-mark">{traceOpen ? "−" : "+"}</span>
            数据与证据（SQL {view.sqlVersions.length} 版 · 对账 {view.reconciliation.length} 项
            {view.evidence.length > 0 ? ` · 来源 ${view.evidence.length}` : ""}）
          </button>
          {traceOpen && (
            <div className="fold-body">
              {view.sqlVersions.map((item, index) => (
                <div key={index} className={item.rejected ? "sql rejected" : "sql ok"}>
                  <div className="sql-head">
                    <span>
                      查询 v{index + 1}
                      {item.rejected ? " · 未通过校验" : " · 已通过校验"}
                    </span>
                    {item.source && <span className="mono src">{item.source}</span>}
                  </div>
                  <pre>{item.sql}</pre>
                  {item.rejected && <div className="reject-reason">{item.rejected}</div>}
                </div>
              ))}
              {view.table && (
                <>
                  <div className="table-src muted">
                    {view.table.source ?? "查询结果"}
                  </div>
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
                </>
              )}
              {view.reconciliation.map((item, index) => (
                <div key={index} className={item.passed ? "recon ok" : "recon fail"}>
                  <span className={item.passed ? "mark ok" : "mark fail"}>
                    {item.passed ? "通过" : "未通过"}
                  </span>
                  对账 {item.rule}：{item.detail}
                </div>
              ))}
              {view.evidence.length > 0 && (
                <div className="evidence-row">
                  {view.evidence.map((item, index) => (
                    <span key={index} className="evidence-chip" title={item.digest}>
                      {item.kind} · {item.ref}
                    </span>
                  ))}
                </div>
              )}
              {view.sqlVersions.length === 0 && (
                <div className="muted">尚未产生查询。</div>
              )}
            </div>
          )}
        </section>
      </div>

      <aside className="side-col">
        <section className="card">
          <div className="card-label">运行状态</div>
          <div className="stage-line">{stage}</div>
          <ul className="audit">
            {events.slice(-6).map((item) => (
              <li key={item.cursor}>
                <span className="mono dim">#{item.cursor}</span> {item.eventType}
              </li>
            ))}
            {events.length === 0 && <li className="muted">等待事件…</li>}
          </ul>
          {events.length > 6 && (
            <div className="muted small">更早事件见审计追溯（{events.length} 条）。</div>
          )}
        </section>
      </aside>
    </div>
  );
}
