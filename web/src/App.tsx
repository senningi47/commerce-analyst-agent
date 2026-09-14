import { useEffect, useMemo, useState } from "react";
import { buildView } from "./view";
import { DEMO_STAGES, SCRIPT, eventsForStage, SUGGESTED_QUESTIONS } from "./mock/stream";
import { STAGE_LABELS, type LoopStage, type RunEvent } from "./types";
import { Workbench } from "./components/Workbench";
import { ApprovalsPage } from "./components/ApprovalsPage";
import { EvalCenterPage } from "./components/EvalCenterPage";

type Tab = "workbench" | "approvals" | "eval";

export default function App() {
  const [tab, setTab] = useState<Tab>("workbench");
  const [stage, setStage] = useState<LoopStage>("completed");
  const [playing, setPlaying] = useState(false);
  const [question, setQuestion] = useState("");

  const events: RunEvent[] = useMemo(() => eventsForStage(stage), [stage]);
  const view = useMemo(() => buildView(events), [events]);

  useEffect(() => {
    const go = () => setTab("approvals");
    window.addEventListener("goto-approvals", go);
    return () => window.removeEventListener("goto-approvals", go);
  }, []);

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

  const ask = (text: string) => {
    setQuestion(text);
    setPlaying(false);
    setStage("clarifying");
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          CommerceAnalyst <span className="brand-sub">电商经营分析工作台</span>
        </div>
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
        <div className="connection muted small mono">模拟数据 · {events.length}/{SCRIPT.length} 事件</div>
      </header>

      {tab === "workbench" && (
        <section className="askbar">
          <input
            className="ask-input"
            placeholder="输入一个经营问题，例如：上周华北 GMV 为什么下滑？"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && question.trim()) ask(question.trim());
            }}
          />
          <button className="btn-primary" onClick={() => question.trim() && ask(question.trim())}>
            开始分析
          </button>
          <div className="suggestions">
            <span className="muted small">快速上手：</span>
            {SUGGESTED_QUESTIONS.map((item) => (
              <button key={item.question} className="suggest" onClick={() => ask(item.question)}>
                {item.question}
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="democtl">
        <span className="muted small">演示控制</span>
        <button className="play" onClick={() => setPlaying((value) => !value)}>
          {playing ? "暂停" : "自动播放"}
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
          <ApprovalsPage stage={stage} proposal={view.proposal} decision={view.decision} />
        )}
        {tab === "eval" && <EvalCenterPage />}
      </main>

      <footer className="footnote">
        Demo（模拟数据）· 演示控制条仅用于评审时切换状态，真实产品中状态由运行事件驱动。
      </footer>
    </div>
  );
}
