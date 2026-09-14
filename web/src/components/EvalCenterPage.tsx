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
    reward: 0.0,
    agentCost: "¥1.4932",
    rounds: "675 turn",
    note: "c: ask_user×539 / submit×1；a: submit×10 未过评审",
  },
  {
    experiment: "pilot-day5-20260913d (分支统计)",
    purpose: "c ×10",
    sets: 10,
    succeeded: 9,
    failed: 1,
    unfinished: 0,
    reward: 0.0,
    agentCost: "≈¥1.00",
    rounds: "60 turn/集",
    note: "澄清循环失控（修复见 prompt-policies v3）",
  },
  {
    experiment: "pilot-day5-20260913d (分支统计)",
    purpose: "a ×10",
    sets: 10,
    succeeded: 9,
    failed: 0,
    unfinished: 1,
    reward: 0.0,
    agentCost: "≈¥0.49",
    rounds: "18 coin/集",
    note: "探索挤占提交，SQL 未先验证",
  },
];

export function EvalCenterPage() {
  return (
    <div className="evalcenter">
      <section className="panel">
        <h3>评测中心（只读）</h3>
        <p className="muted">
          数据源：eval 表经只读 API（Step 3 接线）。下表为 Pilot 实测口径。
        </p>
        <table className="result eval">
          <thead>
            <tr>
              <th>实验</th>
              <th>目的/分支</th>
              <th>集数</th>
              <th>succeeded</th>
              <th>failed</th>
              <th>unfinished</th>
              <th>reward</th>
              <th>agent 费用</th>
              <th>轮次</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            {PILOT_ROWS.map((row, index) => (
              <tr key={index}>
                <td>{row.experiment}</td>
                <td>{row.purpose}</td>
                <td>{row.sets}</td>
                <td>{row.succeeded}</td>
                <td>{row.failed}</td>
                <td>{row.unfinished}</td>
                <td className="zero">{row.reward.toFixed(1)}</td>
                <td>{row.agentCost}</td>
                <td>{row.rounds}</td>
                <td className="muted">{row.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="notice warn">
          预算门 FAIL（457.6 元 / 160 元线）· 能力门 FAIL（rewards 全 0）——两门为
          Day 6 输入基线，修复后以 Task 10 小样本实测更新。
        </div>
      </section>
    </div>
  );
}
