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
  const pending = stage === "awaiting_approval";
  const rejected = stage === "approval_rejected";
  return (
    <div className="approvals">
      <section className="panel">
        <h3>提案审批（运营关键路径）</h3>
        {!proposal && <div className="notice muted-note">暂无待审批提案。</div>}
        {proposal && (
          <div className="proposal">
            <div className="prop-ref">{proposal.ref}</div>
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
            <div className="actions">
              <button className="approve" disabled={!pending}>
                批准
              </button>
              <button className="reject" disabled={!pending}>
                拒绝
              </button>
              <input
                className="annotate"
                placeholder="审批标注（写入 risk_annotations 的公开摘要）"
              />
            </div>
            <p className="muted">
              Demo：按钮随状态启用；真实实现经审批 API + HMAC 审批人身份。
            </p>
          </div>
        )}
        {decision && (
          <div className={rejected ? "decision rejected" : "decision ok"}>
            最近决定 <b>{decision.code}</b>：{decision.text}
          </div>
        )}
      </section>
    </div>
  );
}
