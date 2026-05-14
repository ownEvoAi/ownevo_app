// Demo payloads for the primitive showcase page. Lifted from
// www/preview/s26-rk7p3/27-primitives.html so the live renderer
// matches the marketing reference. These are NOT used by any live
// workflow page — live data comes from apps/web/lib/primitive-data-resolver.ts.

import type {
  AlertItem,
  ConversationData,
  DocumentData,
  KanbanData,
  MetricCardDatum,
  ScheduleData,
  SideBySideData,
  TableData,
  TimeSeriesData,
} from '@/app/components/primitives'

export const METRIC_CARDS_DEMO: MetricCardDatum[] = [
  {
    label: 'Forecast accuracy',
    value: '91.2',
    unit: '%',
    delta: { value: '5.4 pts', direction: 'up', scope: 'vs baseline' },
  },
  {
    label: 'SKU coverage',
    value: '2,184',
    delta: { value: 'no change', direction: 'flat', scope: '· 30d' },
  },
  {
    label: 'Stockout exposure',
    value: '$84k',
    delta: { value: '21%', direction: 'down', scope: 'week-over-week' },
  },
  {
    label: 'Markdown risk',
    value: '$12k',
    delta: { value: '8%', direction: 'down', scope: 'week-over-week' },
  },
]

export const TIME_SERIES_DEMO: TimeSeriesData = {
  title: 'Forecast accuracy · last 30 days',
  subtitle: '↑ 5.4 pts vs baseline · 4 improvements',
  series: [
    {
      name: 'Forecast accuracy',
      points: [
        { t: 'D1', value: 85.8 },
        { t: 'D6', value: 85.6 },
        { t: 'D10', value: 87.4 },
        { t: 'D14', value: 87.6 },
        { t: 'D18', value: 89.3 },
        { t: 'D22', value: 89.5 },
        { t: 'D26', value: 90.7 },
        { t: 'D29', value: 91.0 },
        { t: 'D30', value: 91.2 },
      ],
    },
  ],
  baseline: 85.8,
  baseline_label: 'baseline 85.8%',
  y_format: 'percent',
}

export const TABLE_DEMO: TableData = {
  title: 'SKUs at risk · region: Pacific NW',
  summary: '4 of 1,847 · sorted by exposure',
  columns: [
    { key: 'sku', label: 'SKU' },
    { key: 'desc', label: 'Description' },
    { key: 'forecast', label: 'Forecast', type: 'number', format: 'integer' },
    { key: 'onhand', label: 'On-hand', type: 'number', format: 'integer' },
    { key: 'exposure', label: 'Exposure' },
    { key: 'risk', label: 'Risk', type: 'pill' },
  ],
  rows: [
    { sku: 'BOOT-W42', desc: "Winter trail boot, men's", forecast: 1847, onhand: 312, exposure: '$48k', risk: 'High' },
    { sku: 'BOOT-W41', desc: "Winter trail boot, women's", forecast: 1422, onhand: 280, exposure: '$22k', risk: 'Med' },
    { sku: 'JKT-D08', desc: 'Down jacket, unisex', forecast: 911, onhand: 198, exposure: '$11k', risk: 'Med' },
    { sku: 'GLV-T03', desc: 'Touchscreen glove', forecast: 540, onhand: 120, exposure: '$3k', risk: 'Low' },
  ],
}

export const ALERT_DEMO: AlertItem[] = [
  {
    severity: 'high',
    title: 'Stockout forecast: BOOT-W42 will hit 0 in 6 days',
    meta: 'Region Pacific NW · revenue exposure $48k · fired 14 min ago',
  },
  {
    severity: 'medium',
    title: 'Promotional uplift underweighted on bundled SKUs',
    meta: '9 traces clustered · proposal generating · fired 1h ago',
  },
  {
    severity: 'low',
    title: 'Supplier lead time drifted +2 days for 12-week SKUs',
    meta: 'Awaiting more signal · 6 traces · fired 4h ago',
  },
]

export const KANBAN_DEMO: KanbanData = {
  columns: [
    { key: 'new', label: 'New', count: 3 },
    { key: 'drafted', label: 'Drafted', count: 2 },
    { key: 'sent', label: 'Sent', count: 14 },
    { key: 'escalated', label: 'Escalated', count: 1 },
  ],
  cards: [
    {
      id: '4821',
      column_key: 'new',
      title: 'Refund eligibility · #4821',
      body: 'Annual subscription, 47 days in',
      meta: 'chat · 2m',
      tags: [{ label: 'policy', tone: 'outline' }],
    },
    {
      id: '4820',
      column_key: 'new',
      title: 'Order status · #4820',
      body: 'Where is my package, no tracking',
      meta: 'email · 4m',
    },
    {
      id: '4815',
      column_key: 'drafted',
      title: 'Account merge · #4815',
      body: 'Two accounts, same email',
      meta: 'chat · 12m',
      tags: [{ label: 'review', tone: 'amber' }],
    },
    {
      id: '4812',
      column_key: 'drafted',
      title: 'Plan change · #4812',
      body: 'Downgrade from team to solo',
      meta: 'email · 18m',
    },
    {
      id: '4809',
      column_key: 'sent',
      title: 'Order confirmation · #4809',
      body: 'Replied 6 minutes ago',
      meta: 'chat · 25m',
      tags: [{ label: 'resolved', tone: 'green' }],
    },
    {
      id: '4807',
      column_key: 'sent',
      title: 'Tracking link · #4807',
      body: 'Replied 12 minutes ago',
      meta: 'email · 31m',
    },
    {
      id: '4798',
      column_key: 'escalated',
      title: 'Refund dispute · #4798',
      body: 'Customer not satisfied with policy',
      meta: 'chat · 1h',
      tags: [{ label: 'tier-2', tone: 'red' }],
    },
  ],
}

export const SCHEDULE_DEMO: ScheduleData = {
  rows: [
    { key: 'morning', label: 'Morning', sub: '06–14' },
    { key: 'afternoon', label: 'Afternoon', sub: '14–22' },
  ],
  cols: [
    { key: 'mon', label: 'Mon', sub: 'May 4' },
    { key: 'tue', label: 'Tue', sub: 'May 5' },
    { key: 'wed', label: 'Wed', sub: 'May 6' },
    { key: 'thu', label: 'Thu', sub: 'May 7' },
    { key: 'fri', label: 'Fri', sub: 'May 8' },
  ],
  cells: [
    { row_key: 'morning', col_key: 'mon', value: 22, target: 22, status: 'ok', tag: '✓ ok' },
    { row_key: 'morning', col_key: 'tue', value: 19, target: 22, status: 'warn', tag: '−3 short' },
    { row_key: 'morning', col_key: 'wed', value: 22, target: 22, status: 'ok', tag: '✓ ok' },
    { row_key: 'morning', col_key: 'thu', value: 26, target: 26, status: 'error', tag: 'cert gap' },
    { row_key: 'morning', col_key: 'fri', value: 22, target: 22, status: 'ok', tag: '✓ ok' },
    { row_key: 'afternoon', col_key: 'mon', value: 26, target: 26, status: 'ok', tag: '✓ ok' },
    { row_key: 'afternoon', col_key: 'tue', value: 26, target: 26, status: 'ok', tag: '✓ ok' },
    { row_key: 'afternoon', col_key: 'wed', value: 26, target: 26, status: 'ok', tag: '✓ ok' },
    { row_key: 'afternoon', col_key: 'thu', value: 26, target: 26, status: 'ok', tag: '✓ ok' },
    { row_key: 'afternoon', col_key: 'fri', value: 26, target: 26, status: 'ok', tag: '✓ ok' },
  ],
}

export const CONVERSATION_DEMO: ConversationData = {
  messages: [
    {
      role: 'user',
      author: 'Customer',
      ts: '12:04',
      text:
        "Hi — I'd like to cancel my annual plan and get a refund for the unused months.",
    },
    {
      role: 'agent',
      author: 'Agent',
      ts: '12:04',
      text:
        'I can help cancel your plan. On refunds — our annual plan is non-refundable after the first 30 days, but I can stop it from auto-renewing. Want me to do that?',
      citations: [{ id: 1, source: 'refund policy · §3.2' }],
    },
    {
      role: 'user',
      author: 'Customer',
      ts: '12:05',
      text: 'OK please stop auto-renew. When does it actually end?',
    },
    {
      role: 'agent',
      author: 'Agent',
      ts: '12:05',
      text: "Done — auto-renew is off. Your current term ends 2026-08-14. You'll keep full access until then.",
      citations: [
        { id: 1, source: 'subscription record' },
        { id: 2, source: 'plan terms' },
      ],
    },
  ],
}

const SIDE_LEFT_BODY = `when forecasting: load 36mo
history. detect anomalies as
>2 stdev from prior-year.
emit forecast.run with
{accuracy, exposure}.`

const SIDE_RIGHT_BODY = `when forecasting: load 36mo
history. detect anomalies as
>2 stdev from prior-year.
also flag seasonal-cluster
drift >15% vs prior-year same-
week (catches winter footwear
case).
emit forecast.run with
{accuracy, exposure}.`

// Highlight the inserted block in the right panel ("also flag …" → "case).")
const ADD_START = SIDE_RIGHT_BODY.indexOf('also flag')
const ADD_END = SIDE_RIGHT_BODY.indexOf('case).') + 'case).'.length

export const SIDE_BY_SIDE_DEMO: SideBySideData = {
  left: {
    title: 'Current skill · v3',
    body: SIDE_LEFT_BODY,
    format: 'code',
  },
  right: {
    title: 'Proposed · v4',
    body: SIDE_RIGHT_BODY,
    format: 'code',
    highlights: [{ start: ADD_START, end: ADD_END, kind: 'added' }],
  },
}

const DOC_PARA_1 =
  'Either party may assign its rights or delegate its duties under this Agreement, in whole or in part, to any successor entity arising from a merger, acquisition, or sale of substantially all of its assets, without the consent of the other party, provided that such successor expressly assumes all obligations hereunder.'

const DOC_PARA_2 =
  'For all other assignments, the prior written consent of the non-assigning party shall be required, which consent shall not be unreasonably withheld, conditioned, or delayed.'

const DOC_PARA_3 = 'Any purported assignment in violation of this Section shall be null and void.'

export const DOCUMENT_DEMO: DocumentData = {
  section_label: '§14.3 · Successor and Assigns',
  blocks: [
    {
      kind: 'para',
      text: DOC_PARA_1,
      spans: [
        {
          start: DOC_PARA_1.indexOf(
            'any successor entity arising from a merger, acquisition, or sale of substantially all of its assets',
          ),
          end:
            DOC_PARA_1.indexOf(
              'any successor entity arising from a merger, acquisition, or sale of substantially all of its assets',
            ) +
            'any successor entity arising from a merger, acquisition, or sale of substantially all of its assets'.length,
          kind: 'flagged',
          note: 'Broader than precedent',
        },
      ],
    },
    {
      kind: 'para',
      text: DOC_PARA_2,
      spans: [
        {
          start: DOC_PARA_2.indexOf(
            'written consent of the non-assigning party shall be required',
          ),
          end:
            DOC_PARA_2.indexOf(
              'written consent of the non-assigning party shall be required',
            ) +
            'written consent of the non-assigning party shall be required'.length,
          kind: 'standard',
          note: 'Standard consent language',
        },
      ],
    },
    { kind: 'para', text: DOC_PARA_3 },
  ],
  annotations: [
    {
      id: 'a1',
      severity: 'medium',
      title: 'Successor carve-out',
      body: 'Confidence 0.62 — broader than precedent. Suggest narrowing to "controlling interest acquisition."',
    },
    {
      id: 'a2',
      severity: 'low',
      title: 'Consent standard',
      body: 'Standard "not unreasonably withheld" language present. ✓',
    },
  ],
}
