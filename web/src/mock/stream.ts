/**
 * 模拟事件流：一条完整的产品闭环脚本（与 Task 7 SSE 事件面同词表），
 * 按 §20 状态子集切片——点选状态条即重放该前缀，自动播放则顺序推进。
 */
import type { LoopStage, RunEvent } from "../types";

const ATTEMPT = "00000000-0000-0000-0000-000000000401";
const T0 = "2026-09-14T09:00:00+00:00";

let cursor = 0;
const event = (
  eventType: RunEvent["eventType"],
  at: string,
  demo: RunEvent["demo"],
  extra: Partial<RunEvent> = {},
): RunEvent => {
  cursor += 1;
  return {
    cursor,
    sequence: cursor - 1,
    attemptId: ATTEMPT,
    eventType,
    status: "succeeded",
    occurredAt: at,
    decisionSummary: null,
    proposalRef: null,
    executionRef: null,
    evidence: [],
    demo,
    ...extra,
  };
};

export const SCRIPT: RunEvent[] = [
  event(
    "clarification_requested",
    T0,
    {
      question: "上周华北地区的 GMV 是多少？为什么环比下滑？",
      clarification: {
        question: "「华北」按配送口径还是销售口径统计？",
        answer: "按客户收货地（销售口径）。",
      },
    },
    { evidence: [{ kind: "知识库", ref: "metric:gmv", digest: "a".repeat(8) + "…" }] },
  ),
  event("clarification_requested", "2026-09-14T09:01:00+00:00", {
    clarification: {
      question: "「上周」指自然周（周一至周日）还是近 7 天？",
      answer: "自然周。",
    },
  }),
  event("investigation_plan_accepted", "2026-09-14T09:02:00+00:00", {
    planStep: { id: "step-1", title: "按收货地聚合上周 GMV，对比上周同期", status: "done" },
  }),
  event("sql_generated", "2026-09-14T09:03:00+00:00", {
    sql: {
      sql: "SELECT SUM(payment_value) FROM orders WHERE region = '华北'",
      rejected: 'column "region" does not exist（订单表无地区字段，需关联客户表收货地）',
      source: "retail.orders × retail.customers",
    },
  }),
  event("sql_repaired", "2026-09-14T09:04:00+00:00", {
    sql: {
      sql: [
        "SELECT c.state AS region, SUM(o.payment_value) AS gmv",
        "FROM orders o JOIN customers c ON c.customer_id = o.customer_id",
        "WHERE o.order_purchase_at >= '2026-09-07'",
        "  AND o.order_purchase_at < '2026-09-14'",
        "GROUP BY c.state",
      ].join("\n"),
      source: "retail.orders × retail.customers（只读查询）",
    },
  }),
  event("query_executed", "2026-09-14T09:05:00+00:00", {
    table: {
      columns: ["region", "gmv", "orders"],
      rows: [
        { region: "华北-北京", gmv: 412_800, orders: 1_932 },
        { region: "华北-天津", gmv: 158_400, orders: 801 },
        { region: "华北-河北", gmv: 96_200, orders: 517 },
      ],
      source: "查询结果 · 实时计算于 retail 库",
    },
    metrics: [
      { label: "上周 GMV", value: "¥667,400", tone: "neutral" },
      { label: "环比变化", value: "-9.8%", delta: "上周 ¥739,900", tone: "down" },
      { label: "订单量", value: "3,250", delta: "上周 3,540", tone: "down" },
    ],
  }),
  event("query_reconciled", "2026-09-14T09:06:00+00:00", {
    chart: [
      { day: "周一", gmv: 92_000, lastWeek: 118_000 },
      { day: "周二", gmv: 88_500, lastWeek: 109_400 },
      { day: "周三", gmv: 95_200, lastWeek: 101_200 },
      { day: "周四", gmv: 84_100, lastWeek: 99_800 },
      { day: "周五", gmv: 103_600, lastWeek: 96_500 },
      { day: "周六", gmv: 118_900, lastWeek: 121_300 },
      { day: "周日", gmv: 85_100, lastWeek: 112_000 },
    ],
    reconciliation: {
      rule: "总额守恒",
      passed: true,
      detail: "分省求和 667,400 = 订单宽表总额 667,400",
    },
  }),
  event(
    "proposal_created",
    "2026-09-14T09:07:00+00:00",
    {
      commandDiff: [
        { field: "action", from: "—", to: "create_price_review" },
        { field: "scope", from: "—", to: "华北-北京/天津" },
        { field: "coupon_budget", from: "—", to: "¥20,000" },
      ],
      proposalMeta: {
        title: "华北价格复审：针对周日跌幅发起定向促销",
        requester: "运营分析师 · 王琳",
        kind: "create_price_review",
        budgetImpact: "¥20,000（月度促销预算的 13%）",
        evidence: ["周日环比 -24%", "北京/天津贡献跌幅 78%"],
      },
    },
    { proposalRef: "proposal:8f1e…d02:1" },
  ),
  event(
    "decision_recorded",
    "2026-09-14T09:08:00+00:00",
    {},
    {
      decisionSummary: {
        code: "changes_requested",
        text: "预算改为 ¥15,000，并把河北纳入范围后再提交。",
      },
      proposalRef: "proposal:8f1e…d02:1",
    },
  ),
  event("query_executed", "2026-09-14T09:09:00+00:00", {
    conclusion: "证据不足以定位下滑主因：缺少流量与转化漏斗数据，建议接入埋点后重查。",
  }),
  event(
    "proposal_created",
    "2026-09-14T09:10:00+00:00",
    {
      commandDiff: [
        { field: "action", from: "create_price_review", to: "create_price_review" },
        { field: "coupon_budget", from: "¥20,000", to: "¥15,000" },
        { field: "scope", from: "华北-北京/天津", to: "华北-北京/天津/河北" },
      ],
      proposalMeta: {
        title: "华北价格复审（修订版）：预算下调并扩大范围",
        requester: "运营分析师 · 王琳",
        kind: "create_price_review",
        budgetImpact: "¥15,000（月度促销预算的 10%）",
        evidence: ["周日环比 -24%", "审批人要求补充河北区域"],
      },
    },
    { proposalRef: "proposal:8f1e…d02:2" },
  ),
  event(
    "decision_recorded",
    "2026-09-14T09:11:00+00:00",
    {},
    {
      decisionSummary: {
        code: "proposal_approved",
        text: "批准修订版提案，按 ¥15,000 预算执行。",
      },
      proposalRef: "proposal:8f1e…d02:2",
    },
  ),
  event("query_reconciled", "2026-09-14T09:12:00+00:00", {
    reconciliation: {
      rule: "读回一致",
      passed: true,
      detail: "执行读回：受影响订单 2,733，预算扣减 ¥15,000",
    },
  }),
  event(
    "execution_completed",
    "2026-09-14T09:13:00+00:00",
    {},
    { executionRef: "execution:3b7c…9a1" },
  ),
  event("report_completed", "2026-09-14T09:14:00+00:00", {
    conclusion:
      "上周华北 GMV 667,400 元，环比下降 9.8%；主因是周日单日下跌 24%。已批准京津冀价格复审（预算 ¥15,000），执行读回一致。",
  }),
];

export const STAGE_CUTS: Record<LoopStage, number> = {
  empty: 0,
  clarifying: 2,
  sql_rejected: 4,
  sql_repaired: 5,
  insufficient: 10,
  awaiting_approval: 8,
  approval_rejected: 9,
  interrupted: 8,
  resumed: 11,
  completed: SCRIPT.length,
};

export function eventsForStage(stage: LoopStage): RunEvent[] {
  return SCRIPT.slice(0, STAGE_CUTS[stage]);
}

/** 快速上手：建议问题（点击即从空状态推进到对应阶段）。 */
export const SUGGESTED_QUESTIONS: { question: string; stage: LoopStage }[] = [
  { question: "上周华北 GMV 为什么下滑？", stage: "clarifying" },
  { question: "哪些卖家存在停售风险？", stage: "sql_repaired" },
  { question: "本周哪些运营指标异常？", stage: "insufficient" },
];

export const DEMO_STAGES: LoopStage[] = [
  "empty",
  "clarifying",
  "sql_rejected",
  "sql_repaired",
  "insufficient",
  "awaiting_approval",
  "approval_rejected",
  "interrupted",
  "resumed",
  "completed",
];
