import { useEffect, useMemo, useState } from "react";
import { buildView } from "./view";
import {
  DEMO_STAGES,
  SCRIPT,
  eventsForStage,
  stageOf,
} from "./mock/stream";
import { STAGE_LABELS, type LoopStage, type RunEvent } from "./types";
import { Workbench } from "./components/Workbench";
import { ApprovalsPage } from "./components/ApprovalsPage";
import { EvalCenterPage } from "./components/EvalCenterPage";

type Tab = "workbench" | "approvals" | "eval";

export default function App() {
  const [tab, setTab] = useState<Tab>("workbench");
  const [stage, setStage] = useState<LoopStage>("completed");
  const [playing, setPlaying] = useState(false);

  const events: RunEvent[] = useMemo(() => eventsForStage(stage), [stage]);
  const view = useMemo(() => buildView(events), [events]);

  useEffect(() => {
    if (!playing) return;
    const order: LoopStage[] = DEMO_STAGES.filter(
      (item) => item !== "interrupted" && item !== "resumed",
    );
    let index = order.indexOf(stage);
    const timer = window.setInterval(() => {
      index += 1;
      if (index >= order.length) {
        setPlaying(false);
        return;
      }
      setStage(order[index]);
    }, 700);
    return () => window.clearInterval(timer);
  }, [playing, stage]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">CommerceAnalyst <span className="muted">电商经营分析</span></div>
        <nav className="tabs">
          {(
            [
              ["workbench", "分析工作台"],
              ["approvals", "运营审批"],
              ["eval", "评测中心"],
            ] as [Tab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              className={tab === key ? "tab active" : "tab"}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="connection">
          <span className="badge demo">模拟流</span>
          <span className="muted">{events.length}/{SCRIPT.length} 事件</span>
        </div>
      </header>

      <div className="stagerail">
        <button className="play" onClick={() => setPlaying((value) => !value)}>
          {playing ? "⏸ 暂停" : "▶ 自动播放"}
        </button>
        {DEMO_STAGES.map((item) => (
          <button
            key={item}
            className={stage === item ? "chip active" : "chip"}
            onClick={() => {
              setPlaying(false);
              setStage(item);
            }}
          >
            {STAGE_LABELS[item]}
          </button>
        ))}
      </div>

      <main className="content">
        {tab === "workbench" && <Workbench stage={stage} view={view} events={events} />}
        {tab === "approvals" && (
          <ApprovalsPage
            stage={stage}
            proposal={view.proposal}
            decision={view.decision}
          />
        )}
        {tab === "eval" && <EvalCenterPage />}
      </main>

      <footer className="footnote">
        Demo（模拟数据）· 状态条驱动全状态切换 · 自动播放按事件时间轴推进 ·
        当前阶段：<b>{STAGE_LABELS[stage]}</b>
        {stageOf(events) === stage ? "" : "（强制覆盖）"}
      </footer>
    </div>
  );
}
