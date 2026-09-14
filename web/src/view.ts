/** 从事件序列投影工作台视图模型（Demo 与真实 SSE 共用）。 */
import type { DemoPayload, LoopStage, RunEvent } from "./types";

export interface Metric {
  label: string;
  value: string;
  delta?: string;
  tone?: "neutral" | "down" | "up";
}

export interface SqlVersion {
  sql: string;
  rejected?: string;
  source?: string;
  /** 真实 SSE 是 summary-only：没有 SQL 文本时以双指纹+原因呈现。 */
  fingerprints?: string[];
}

export interface ViewModel {
  question: string | null;
  clarifications: { question: string; answer?: string }[];
  planSteps: { id: string; title: string; status: string }[];
  metrics: Metric[];
  sqlVersions: SqlVersion[];
  table: {
    columns: string[];
    rows: Record<string, string | number>[];
    source?: string;
  } | null;
  chart: { day: string; gmv: number; lastWeek: number }[];
  reconciliation: { rule: string; passed: boolean; detail: string }[];
  conclusion: string | null;
  insufficient: boolean;
  proposal: {
    ref: string;
    diff: { field: string; from: string; to: string }[];
    meta: DemoPayload["proposalMeta"] | null;
  } | null;
  decision: { code: string; text: string } | null;
  executionRef: string | null;
  evidence: { kind: string; ref: string; digest: string }[];
}

const EMPTY: ViewModel = {
  question: null,
  clarifications: [],
  planSteps: [],
  metrics: [],
  sqlVersions: [],
  table: null,
  chart: [],
  reconciliation: [],
  conclusion: null,
  insufficient: false,
  proposal: null,
  decision: null,
  executionRef: null,
  evidence: [],
};

function sourceOf(event: RunEvent): string | undefined {
  const refs = (event.evidence ?? []).map((item) => item.ref);
  return refs.length > 0 ? refs.join(" · ") : undefined;
}

/** 真实 SSE 是 summary-only：事件公开面 → 视图模型的等价投影。 */
function applySummaryEvent(view: ViewModel, item: RunEvent): void {
  switch (item.eventType) {
    case "clarification_requested":
      if (item.decisionSummary) {
        view.clarifications.push({ question: item.decisionSummary.text });
      }
      break;
    case "investigation_plan_accepted":
      if (item.decisionSummary) {
        view.planSteps.push({
          id: `live-${item.cursor}`,
          title: item.decisionSummary.text,
          status: "done",
        });
      }
      break;
    case "sql_generated":
    case "sql_repaired":
      view.sqlVersions.push({
        sql: "",
        rejected:
          item.status === "failed" ? (item.reasonCode ?? "校验未通过") : undefined,
        fingerprints: item.queryFingerprints ?? [],
        source: sourceOf(item),
      });
      break;
    case "query_reconciled":
      if (item.decisionSummary) {
        view.reconciliation.push({
          rule: "对账",
          passed: item.status !== "failed",
          detail: item.decisionSummary.text,
        });
      }
      break;
    case "report_completed":
      if (item.decisionSummary) {
        view.conclusion = item.decisionSummary.text;
        view.insufficient = item.decisionSummary.code.includes("insufficient");
      }
      break;
    default:
      break;
  }
}

export function buildView(events: RunEvent[]): ViewModel {
  const view: ViewModel = {
    ...EMPTY,
    clarifications: [],
    planSteps: [],
    metrics: [],
    sqlVersions: [],
    reconciliation: [],
    evidence: [],
  };
  for (const item of events) {
    const demo: DemoPayload = item.demo ?? {};
    if (demo.question) view.question = demo.question;
    if (demo.clarification) view.clarifications.push(demo.clarification);
    if (demo.planStep) view.planSteps.push(demo.planStep);
    if (demo.metrics) view.metrics = demo.metrics;
    if (demo.sql) view.sqlVersions.push(demo.sql);
    if (demo.table) view.table = demo.table;
    if (demo.chart) view.chart = demo.chart;
    if (demo.reconciliation) view.reconciliation.push(demo.reconciliation);
    if (demo.conclusion) {
      view.conclusion = demo.conclusion;
      view.insufficient = demo.conclusion.startsWith("证据不足");
    }
    if (
      !demo.sql &&
      (item.eventType === "sql_generated" || item.eventType === "sql_repaired")
    ) {
      applySummaryEvent(view, item);
    } else if (!demo.conclusion && item.eventType === "report_completed") {
      applySummaryEvent(view, item);
    } else if (
      !demo.reconciliation &&
      !demo.table &&
      item.eventType === "query_reconciled"
    ) {
      applySummaryEvent(view, item);
    } else if (
      !demo.clarification &&
      !demo.planStep &&
      (item.eventType === "clarification_requested" ||
        item.eventType === "investigation_plan_accepted")
    ) {
      applySummaryEvent(view, item);
    }
    if (item.evidence) view.evidence.push(...item.evidence);
    if (item.proposalRef && demo.commandDiff) {
      view.proposal = {
        ref: item.proposalRef,
        diff: demo.commandDiff,
        meta: demo.proposalMeta ?? null,
      };
    } else if (item.proposalRef && !view.proposal) {
      view.proposal = { ref: item.proposalRef, diff: [], meta: null };
    }
    if (item.decisionSummary) view.decision = item.decisionSummary;
    if (item.executionRef) view.executionRef = item.executionRef;
  }
  return view;
}

/**
 * 真实事件 → §20 状态推导（Task 8 Step 1 种子的共享版）。
 * 返回 null 表示最后一个事件不属于收窄子集里的稳定状态
 * （如执行中/审批刚通过），由调用方回退显示事件类型本身。
 */
export function stageOf(events: RunEvent[]): LoopStage | null {
  if (events.length === 0) return "empty";
  const last = events[events.length - 1];
  if (last.eventType === "report_completed") return "completed";
  if (last.eventType === "recovery_applied") return "resumed";
  if (last.decisionSummary?.code === "changes_requested") return "approval_rejected";
  if (last.eventType === "proposal_created") return "awaiting_approval";
  if (last.demo?.conclusion?.startsWith("证据不足")) return "insufficient";
  if (last.eventType === "sql_repaired") return "sql_repaired";
  if (last.eventType === "sql_generated") return "sql_rejected";
  if (
    last.eventType === "clarification_requested" ||
    last.eventType === "investigation_plan_accepted"
  ) {
    return "clarifying";
  }
  return null;
}
