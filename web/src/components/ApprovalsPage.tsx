import { useState } from "react";
import type { LoopStage } from "../types";
import type { ViewModel } from "../view";

export function ApprovalsPage({
  stage,
  proposal,
  decision,
}: {
  stage: LoopStage;
  proposal: ViewModel["proposal"];
  decision: ViewModel["decision"];
}) {
  const [annotation, setAnnotation] = useState("");
  const pending = stage === "awaiting_approval" && proposal !== null;
  const rejected = decision?.code === "changes_requested";

  return (
    <div className="approvals">
      <section className="card">
        <div className="card-head">
          <div className="card-label">待我审批（{pending ? 1 : 0}）</div>
          <div className="card-src mono">proposal:8f1e…d02:2 · revision 2</div>
        </div>

        {!proposal && (
          <div className="empty-note">当前没有待审批提案。分析完成并创建提案后会出现在这里。</div>
        )}

        {proposal && (
          <>
            <h2 className="proposal-title">{proposal.meta?.title ?? "运营动作提案"}</h2>
            <div className="prop-meta">
              <span>发起 {proposal.meta?.requester}</span>
              <span>类型 <span className="mono">{proposal.meta?.kind}</span></span>
              <span>预算影响 <b>{proposal.meta?.budgetImpact}</b></span>
            </div>

            <div className="section-label">变更内容</div>
            <table className="diff">
              <thead>
                <tr>
                  <th>字段</th>
                  <th>修订前</th>
                  <th>修订后</th>
                </tr>
              </thead>
              <tbody>
                {proposal.diff.map((row) => (
                  <tr key={row.field}>
                    <td>{row.field}</td>
                    <td className={row.from === "—" ? "dim" : "del"}>{row.from}</td>
                    <td className="add">{row.to}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="section-label">决策依据</div>
            <ul className="basis">
              {(proposal.meta?.evidence ?? []).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <div className="muted small">
              证据链可在分析工作台「数据与证据」中逐条溯源（SQL、对账、知识来源）。
            </div>

            <div className="section-label">审批标注{!pending && "（历史记录）"}</div>
            <input
              className="annotate"
              placeholder="驳回时必填：说明需要修订的内容"
              value={annotation}
              onChange={(e) => setAnnotation(e.target.value)}
            />
            <div className="actions">
              <button className="btn-primary" disabled={!pending}>
                批准执行
              </button>
              <button
                className="btn-quiet"
                disabled={!pending || annotation.trim() === ""}
                title="驳回必须在标注中说明原因"
              >
                驳回
              </button>
              <span className="muted small self-center">
                审批身份经 HMAC 密钥签名，写入不可篡改审计。
              </span>
            </div>
          </>
        )}

        {decision && (
          <div className={rejected ? "decision rejected" : "decision ok"}>
            <span className={rejected ? "mark fail" : "mark ok"}>
              {rejected ? "已驳回" : "已批准"}
            </span>
            {decision.text}
          </div>
        )}
      </section>
    </div>
  );
}
