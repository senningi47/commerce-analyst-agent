/**
 * 只读 API 客户端：运行列表、eval 只读数据、真实 SSE 事件流。
 *
 * EventSource 原生携带 Last-Event-ID 自动重连（服务端按游标重放）；
 * 服务端事件块带 `event:` 字段，因此按 11 种事件类型分别订阅。
 */
import type { RunEvent, RunEventType } from "./types";

export const EVENT_TYPES: RunEventType[] = [
  "clarification_requested",
  "investigation_plan_accepted",
  "sql_generated",
  "sql_repaired",
  "query_executed",
  "query_reconciled",
  "proposal_created",
  "decision_recorded",
  "execution_completed",
  "report_completed",
  "recovery_applied",
];

export interface RunSummary {
  run_id: string;
  started_at: string;
  last_event_at: string;
  event_count: number;
}

export interface EvalExperimentSummary {
  experiment_id: string;
  purpose: string;
  config_hash: string;
  created_at: string;
  closed_at: string | null;
  attempt_total: number;
  status_counts: Record<string, number>;
  reward_total: string;
  agent_cost_total: string;
}

export interface EvalAttemptRecord {
  attempt_id: string;
  run_id: string;
  experiment_id: string;
  task_id: string;
  mode: string;
  attempt_seq: number;
  status: string;
  error_class: string | null;
  started_at: string;
  finished_at: string | null;
  reward: string | null;
  phase1_passed: boolean | null;
  phase2_passed: boolean | null;
  rounds: number | null;
  tool_calls: number | null;
  submit_count: number | null;
  agent_cost_amount: string | null;
  simulator_cost_amount: string | null;
  agent_turns: number | null;
}

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`GET ${path} -> ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchRuns(): Promise<RunSummary[]> {
  return getJson<RunSummary[]>("/api/runs");
}

export function fetchEvalExperiments(): Promise<EvalExperimentSummary[]> {
  return getJson<EvalExperimentSummary[]>("/api/eval/experiments");
}

export function fetchEvalAttempts(
  experimentId: string,
): Promise<EvalAttemptRecord[]> {
  return getJson<EvalAttemptRecord[]>(
    `/api/eval/experiments/${encodeURIComponent(experimentId)}/attempts`,
  );
}

/** SSE wire payload (snake_case) → 前端 RunEvent（camelCase）。 */
export function parseSseEvent(data: string): RunEvent {
  const raw = JSON.parse(data) as Record<string, unknown>;
  const evidence = (raw.evidence ?? []) as Record<string, string>[];
  return {
    cursor: raw.cursor as number,
    sequence: raw.sequence as number,
    attemptId: raw.attempt_id as string,
    eventType: raw.event_type as RunEventType,
    status: raw.status as RunEvent["status"],
    occurredAt: raw.occurred_at as string,
    reasonCode: (raw.reason_code as string | null) ?? null,
    decisionSummary: (raw.decision_summary as RunEvent["decisionSummary"]) ?? null,
    proposalRef: (raw.proposal_ref as string | null) ?? null,
    executionRef: (raw.execution_ref as string | null) ?? null,
    auditRef: (raw.audit_ref as string | null) ?? null,
    evidence: evidence.map((item) => ({
      kind: item.kind,
      ref: item.ref,
      digest: item.digest,
    })),
    queryFingerprints: (raw.query_fingerprints as string[]) ?? [],
  };
}

export interface RunStreamHandle {
  close(): void;
}

/** 订阅一个运行的真实事件流；断线由 EventSource 原生重连（Last-Event-ID）。 */
export function openRunStream(
  runId: string,
  handlers: {
    onEvent: (event: RunEvent) => void;
    onState: (state: ConnectionState) => void;
  },
): RunStreamHandle {
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  handlers.onState("connecting");
  source.onopen = () => handlers.onState("open");
  source.onerror = () => handlers.onState("reconnecting");
  const forward = (raw: MessageEvent<string>) => {
    handlers.onEvent(parseSseEvent(raw.data));
  };
  for (const type of EVENT_TYPES) {
    source.addEventListener(type, forward as EventListener);
  }
  return {
    close() {
      source.close();
      handlers.onState("idle");
    },
  };
}
