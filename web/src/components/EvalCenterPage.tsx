/**
 * 评测中心（只读）。Demo 展示 Pilot 真实口径（pilot-day5-20260913d），
 * Step 3 将改为对接只读 eval API（rewards 全 0 如实展示）。
 */

const PILOT_ROWS = [
  {
    experiment: "pilot-day5-20260913d",
    purpose: "pilot",
    sets: 20,
    succeeded: 18,
    failed: 1,
    unfinished: 1,
    reward: "0.0",
    agentCost: "¥1.4932",
    rounds: "675 turn",
  },
  {
    experiment: "pilot-day5-20260913d",
    purpose: "c 分支 ×10",
    sets: 10,
    succeeded: 9,
    failed: 1,
    unfinished: 0,
    reward: "0.0",
    agentCost: "≈¥1.00",
    rounds: "60 turn/集",
  },
  {
    experiment: "pilot-day5-20260913d",
    purpose: "a 分支 ×10",
    sets: 10,
    succeeded: 9,
    failed: 0,
    unfinished: 1,
    reward: "0.0",
    agentCost: "≈¥0.49",
    rounds: "18 coin/集",
  },
];

export function EvalCenterPage() {
  return (
    <div className="evalcenter">
      <div className="metrics four">
        <div className="card metric tone-neutral">
          <div className="card-label">评测集</div>
          <div className="metric-value mono">20</div>
          <div className="metric-delta">c ×10 + a ×10</div>
        </div>
        <div className="card metric tone-neutral">
          <div className="card-label">有效完成</div>
          <div className="metric-value mono">18</div>
          <div className="metric-delta">1 failed · 1 unfinished</div>
        </div>
        <div className="card metric tone-down">
          <div className="card-label">平均 reward</div>
          <div className="metric-value mono">0.0</div>
          <div className="metric-delta">策略层问题，通道无缺陷</div>
        </div>
        <div className="card metric tone-neutral">
          <div className="card-label">agent 侧费用</div>
          <div className="metric-value mono">¥1.4932</div>
          <div className="metric-delta">上限 ¥30 · 空闲档计费</div>
        </div>
      </div>

      <section className="card">
        <div className="card-label">运行明细 · pilot-day5-20260913d</div>
        <table className="result">
          <thead>
            <tr>
              <th>目的/分支</th>
              <th>集数</th>
              <th>succeeded</th>
              <th>failed</th>
              <th>unfinished</th>
              <th>reward</th>
              <th>agent 费用</th>
              <th>轮次</th>
            </tr>
          </thead>
          <tbody>
            {PILOT_ROWS.map((row, index) => (
              <tr key={index}>
                <td>{row.purpose}</td>
                <td className="mono">{row.sets}</td>
                <td className="mono">{row.succeeded}</td>
                <td className="mono">{row.failed}</td>
                <td className="mono">{row.unfinished}</td>
                <td className="mono zero">{row.reward}</td>
                <td className="mono">{row.agentCost}</td>
                <td className="mono">{row.rounds}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="stage-note">
          两门基线：预算门 FAIL（457.6 元 / 160 元线）· 能力门 FAIL（rewards 全
          0）。策略修复（prompt-policies v3）后以 Task 10 小样本实测更新本页。
        </div>
      </section>
    </div>
  );
}
