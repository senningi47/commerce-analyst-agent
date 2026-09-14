/** 从事件序列投影工作台视图模型（Demo 与真实 SSE 共用）。 */
import type { DemoPayload, RunEvent } from "./types";

export interface ViewModel {
  question: string | null;
  clarifications: { question: string; answer: string }[];
  planSteps: { id: string; title: string; status: string }[];
  sqlVersions: { sql: string; rejected?: string }[];
  table: { columns: string[]; rows: Record<string, string | number>[] } | null;
  chart: { day: string; gmv: number; lastWeek: number }[];
  reconciliation: { rule: string; passed: boolean; detail: string }[];
  conclusion: string | null;
  insufficient: boolean;
  proposal: { ref: string; diff: { field: string; from: string; to: string }[] } | null;
  decision: { code: string; text: string } | null;
  executionRef: string | null;
  evidence: { kind: string; ref: string; digest: string }[];
}

const EMPTY: ViewModel = {
  question: null,
  clarifications: [],
  planSteps: [],
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

export function buildView(events: RunEvent[]): ViewModel {
  const view: ViewModel = { ...EMPTY, clarifications: [], planSteps: [], sqlVersions: [], reconciliation: [], evidence: [] };
  for (const item of events) {
    const demo: DemoPayload = item.demo ?? {};
    if (demo.question) view.question = demo.question;
    if (demo.clarification) view.clarifications.push(demo.clarification);
    if (demo.planStep) view.planSteps.push(demo.planStep);
    if (demo.sql) view.sqlVersions.push(demo.sql);
    if (demo.table) view.table = demo.table;
    if (demo.chart) view.chart = demo.chart;
    if (demo.reconciliation) view.reconciliation.push(demo.reconciliation);
    if (demo.conclusion) {
      view.conclusion = demo.conclusion;
      view.insufficient = demo.conclusion.startsWith("证据不足");
    }
    if (item.evidence) view.evidence.push(...item.evidence);
    if (item.proposalRef && demo.commandDiff) {
      view.proposal = { ref: item.proposalRef, diff: demo.commandDiff };
    }
    if (item.decisionSummary) view.decision = item.decisionSummary;
    if (item.executionRef) view.executionRef = item.executionRef;
  }
  return view;
}
