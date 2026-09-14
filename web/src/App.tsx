import { useEffect, useMemo, useState } from "react";
import { buildView, stageOf } from "./view";
import {
  fetchRuns,
  openRunStream,
  type ConnectionState,
  type RunStreamHandle,
  type RunSummary,
} from "./api";
import { DEMO_STAGES, SCRIPT, eventsForStage, SUGGESTED_QUESTIONS } from "./mock/stream";
import { STAGE_LABELS, type LoopStage, type RunEvent } from "./types";
import { Workbench } from "./components/Workbench";
import { ApprovalsPage } from "./components/ApprovalsPage";
import { EvalCenterPage } from "./components/EvalCenterPage";

type Tab = "workbench" | "approvals" | "eval";
type Mode = "demo" | "live";

const CONNECTION_LABELS: Record<ConnectionState, string> = {
  idle: "未连接",
  connecting: "连接中…",
  open: "已连接",
  reconnecting: "重连中（Last-Event-ID）",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("workbench");
  const [mode, setMode] = useState<Mode>("demo");

  // 演示模式状态
  const [stage, setStage] = useState<LoopStage>("completed");
  const [playing, setPlaying] = useState(false);
  const [question, setQuestion] = useState("");

  // 实时模式状态
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [runsUnavailable, setRunsUnavailable] = useState(false);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [liveEvents, setLiveEvents] = useState<RunEvent[]>([]);

  const demoEvents: RunEvent[] = useMemo(() => eventsForStage(stage), [stage]);
  const events = mode === "demo" ? demoEvents : liveEvents;
  const view = useMemo(() => buildView(events), [events]);
  const liveStage = useMemo(
    () => (mode === "live" ? stageOf(events) : null),
    [mode, events],
  );

  useEffect(() => {
    const go = () => setTab("approvals");
    window.addEventListener("goto-approvals", go);
    return () => window.removeEventListener("goto-approvals", go);
  }, []);

  const loadRuns = () => {
    setRunsUnavailable(false);
    fetchRuns()
      .then(setRuns)
      .catch(() => setRunsUnavailable(true));
  };

  useEffect(() => {
    if (mode === "live") loadRuns();
  }, [mode]);

  useEffect(() => {
    if (mode !== "live" || !selectedRun) return;
    setLiveEvents([]);
    let handle: RunStreamHandle | null = null;
    // EventSource 原生携带 Last-Event-ID 重连；服务端按游标重放缺失事件。
    handle = openRunStream(selectedRun, {
      onEvent: (event) =>
        setLiveEvents((prev) => {
          // dev StrictMode 双挂载/重放可能产生重复流；cursor 是运行内唯一序。
          if (prev.some((item) => item.cursor === event.cursor)) return prev;
          return [...prev, event];
        }),
      onState: setConnection,
    });
    return () => handle?.close();
  }, [mode, selectedRun]);

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

  const stageLabel =
    mode === "demo"
      ? stage
      : liveStage
        ? STAGE_LABELS[liveStage]
        : events.length > 0
          ? `事件 ${events[events.length - 1].eventType}`
          : "空状态";

  const connectionText =
    mode === "demo"
      ? `模拟数据 · ${events.length}/${SCRIPT.length} 事件`
      : selectedRun
        ? `实时 · ${CONNECTION_LABELS[connection]} · ${events.length} 事件`
        : "实时 · 未选择运行";

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
        <nav className="tabs">
          {(
            [
              ["demo", "演示"],
              ["live", "实时"],
            ] as [Mode, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              className={mode === key ? "tab active" : "tab"}
              onClick={() => {
                setMode(key);
                setTab("workbench");
              }}
              title={
                key === "live"
                  ? "连接只读 API 的真实事件流（SSE + Last-Event-ID 重连）"
                  : "模拟事件流驱动的完整布局演示"
              }
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="connection muted small mono">{connectionText}</div>
      </header>

      {tab === "workbench" && mode === "demo" && (
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

      {tab === "workbench" && mode === "live" && (
        <section className="askbar">
          <select
            className="ask-input"
            value={selectedRun ?? ""}
            onChange={(e) => setSelectedRun(e.target.value || null)}
          >
            <option value="">
              {runsUnavailable
                ? "无法连接运行目录 API（/api/runs）"
                : runs && runs.length > 0
                  ? "选择一个运行以订阅真实事件流…"
                  : "运行目录为空（产品链路尚未产生 Trace）"}
            </option>
            {(runs ?? []).map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.run_id.slice(0, 8)}… · {run.event_count} 事件 ·{" "}
                {new Date(run.last_event_at).toLocaleString()}
              </option>
            ))}
          </select>
          <button className="btn-quiet" onClick={loadRuns}>
            刷新运行
          </button>
          <div className="suggestions muted small">
            事件为 summary-only 公开面（§18）：SQL 以双指纹呈现，富载荷仅演示模式提供。
          </div>
        </section>
      )}

      {mode === "demo" && (
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
      )}

      <main className="content">
        {tab === "workbench" && (
          <Workbench
            stage={mode === "demo" ? stage : liveStage}
            view={view}
            events={events}
            stageLabel={stageLabel}
          />
        )}
        {tab === "approvals" && (
          <ApprovalsPage
            stage={mode === "demo" ? stage : liveStage}
            proposal={view.proposal}
            decision={view.decision}
          />
        )}
        {tab === "eval" && <EvalCenterPage />}
      </main>

      <footer className="footnote">
        {mode === "demo"
          ? "Demo（模拟数据）· 演示控制条仅用于评审时切换状态，实时模式由只读 API 的真实事件流驱动。"
          : "实时模式 · 数据来自只读 API（ops_read 视图，agent_reader 身份）· 断线自动按 Last-Event-ID 重放。"}
      </footer>
    </div>
  );
}
