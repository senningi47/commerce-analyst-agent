/** Mirror of the Day 6 SSE event surface (src/commerce_agent/api/events.py). */

export type RunEventType =
  | "clarification_requested"
  | "investigation_plan_accepted"
  | "sql_generated"
  | "sql_repaired"
  | "query_executed"
  | "query_reconciled"
  | "proposal_created"
  | "decision_recorded"
  | "execution_completed"
  | "report_completed"
  | "recovery_applied";

export interface RunEvent {
  /** Run-scoped reconnect cursor (SSE id). */
  cursor: number;
  /** Attempt-scoped trace sequence (audit correlation). */
  sequence: number;
  attemptId: string;
  eventType: RunEventType;
  status: "started" | "succeeded" | "failed" | "stopped";
  occurredAt: string;
  reasonCode?: string | null;
  decisionSummary?: { code: string; text: string } | null;
  proposalRef?: string | null;
  executionRef?: string | null;
  evidence?: { kind: string; ref: string; digest: string }[];
  /** Demo-only rich payload; the real SSE surface stays summary-only. */
  demo?: DemoPayload;
}

/** §20 收窄子集：工作台需要演示的全部运行状态。 */
export type LoopStage =
  | "empty"
  | "clarifying"
  | "sql_rejected"
  | "sql_repaired"
  | "insufficient"
  | "awaiting_approval"
  | "approval_rejected"
  | "interrupted"
  | "resumed"
  | "completed";

export const STAGE_LABELS: Record<LoopStage, string> = {
  empty: "空状态",
  clarifying: "澄清",
  sql_rejected: "SQL 拒绝",
  sql_repaired: "SQL 修复",
  insufficient: "证据不足",
  awaiting_approval: "待审批",
  approval_rejected: "审批拒绝",
  interrupted: "运行中断",
  resumed: "运行恢复",
  completed: "完成",
};

/** 演示载荷：真实 SSE 是 summary-only，SQL/表格/图表由只读工件 API 提供。 */
export interface DemoPayload {
  question?: string;
  clarification?: { question: string; answer: string };
  planStep?: { id: string; title: string; status: "done" | "pending" };
  metrics?: { label: string; value: string; delta?: string; tone?: Tone }[];
  sql?: { sql: string; rejected?: string; source?: string };
  table?: {
    columns: string[];
    rows: Record<string, string | number>[];
    source?: string;
  };
  chart?: { day: string; gmv: number; lastWeek: number }[];
  reconciliation?: { rule: string; passed: boolean; detail: string };
  conclusion?: string;
  commandDiff?: { field: string; from: string; to: string }[];
  proposalMeta?: {
    title: string;
    requester: string;
    kind: string;
    budgetImpact: string;
    evidence: string[];
  };
}

export type Tone = "neutral" | "down" | "up";
