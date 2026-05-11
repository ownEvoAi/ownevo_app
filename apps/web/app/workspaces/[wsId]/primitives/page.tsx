import type { Metadata } from 'next'
import {
  AlertList,
  ConversationView,
  DocumentReader,
  KanbanBoard,
  MetricCards,
  ScheduleGrid,
  SideBySideView,
  TableView,
  TimeSeriesChart,
} from '@/app/components/primitives'
import {
  ALERT_DEMO,
  CONVERSATION_DEMO,
  DOCUMENT_DEMO,
  KANBAN_DEMO,
  METRIC_CARDS_DEMO,
  SCHEDULE_DEMO,
  SIDE_BY_SIDE_DEMO,
  TABLE_DEMO,
  TIME_SERIES_DEMO,
} from './demo-data'

export const metadata: Metadata = {
  title: 'Views · ownEvo',
}

// 8.0.1 + 8.0.2 — primitive showcase page. Renders every primitive
// with hand-curated demo data lifted from
// www/preview/s26-rk7p3/27-primitives.html so reviewers and the
// NL-gen generator have a live reference of how each primitive
// renders. The runtime data shapes here are the contracts the
// Phase-2 resolver (TODO-35) will produce from agent output.

interface Section {
  id: string
  name: string
  tag: string
  meta: string
  render: React.ReactNode
  spec: {
    inputShape: string
    bestFor: string
    avoidWhen: string
  }
}

const SECTIONS: Section[] = [
  {
    id: 'metric-cards',
    name: 'MetricCards',
    tag: 'demand-prediction · labour',
    meta: 'A row of headline numbers with deltas. Use when the workflow output is a small set of KPIs the operator scans every morning.',
    render: <MetricCards data={METRIC_CARDS_DEMO} />,
    spec: {
      inputShape:
        '{ label, value, unit?, delta?: { value, direction, scope } }[]',
      bestFor: 'Glanceable dashboards · 2–6 KPIs · operator opens, scans, moves on',
      avoidWhen: 'More than 8 metrics (use TableView) or single value with deep drill (use chart)',
    },
  },
  {
    id: 'time-series',
    name: 'TimeSeriesChart',
    tag: 'all workflows',
    meta: 'Line chart with optional baseline reference. The canonical "is the workflow getting better" view.',
    render: <TimeSeriesChart data={TIME_SERIES_DEMO} />,
    spec: {
      inputShape:
        '{ series: { name, points: [{ t, value }] }[], baseline?, baseline_label?, annotations? }',
      bestFor: 'Lift over time · before/after improvement story · YC demo hero',
      avoidWhen: 'Comparing categorical values (use TableView) or single point-in-time state (use MetricCards)',
    },
  },
  {
    id: 'table-view',
    name: 'TableView',
    tag: 'demand-prediction · labour',
    meta: 'Sortable table for row-shaped output. Heavy use in supply chain and labour where the operator works through a list.',
    render: <TableView data={TABLE_DEMO} />,
    spec: {
      inputShape:
        '{ columns: { key, label, type, format? }[], rows: object[], title?, summary? }',
      bestFor: 'Operator triage · "show me the things that need attention, ranked"',
      avoidWhen: 'Fewer than 3 rows (use cards) · narrative output (use ConversationView)',
    },
  },
  {
    id: 'alert-list',
    name: 'AlertList',
    tag: 'all workflows',
    meta: 'Vertically-stacked list of severity-marked items. Inbox and "things fired since last check" view.',
    render: <AlertList data={ALERT_DEMO} />,
    spec: {
      inputShape: '{ severity: "high"|"medium"|"low", title, meta, action_url? }[]',
      bestFor: 'Inbox · monitor signals · anything time-ordered with severity',
      avoidWhen: 'Items have no inherent priority (use TableView)',
    },
  },
  {
    id: 'kanban-board',
    name: 'KanbanBoard',
    tag: 'support · labour',
    meta: 'Cards across stage columns. Ticket-like flows where operators move items through states.',
    render: <KanbanBoard data={KANBAN_DEMO} />,
    spec: {
      inputShape:
        '{ columns: { key, label, count }[], cards: { id, column_key, title, body, meta, tags? }[] }',
      bestFor: 'Ticket triage · ops queues with clear stages · agent + human shared workspace',
      avoidWhen: 'No stage transitions (use TableView or AlertList)',
    },
  },
  {
    id: 'schedule-grid',
    name: 'ScheduleGrid',
    tag: 'labour · capacity-planning',
    meta: '2-D resource × time grid with cell-level status. Shift schedules, content calendars, capacity boards, on-call rotations.',
    render: <ScheduleGrid data={SCHEDULE_DEMO} />,
    spec: {
      inputShape:
        '{ rows: { key, label }[], cols: { key, label, sub? }[], cells: { row_key, col_key, value, target?, status, tag? }[] }',
      bestFor: 'Shift schedules · capacity boards · content calendars · anything resource × time',
      avoidWhen: 'Time isn\'t an axis (use TableView) · single-day view (use AlertList)',
    },
  },
  {
    id: 'conversation',
    name: 'ConversationView',
    tag: 'support · order-intake',
    meta: 'Threaded chat-style render with citation chips. Whenever the workflow output is a reply to a person.',
    render: <ConversationView data={CONVERSATION_DEMO} />,
    spec: {
      inputShape:
        '{ messages: { role: "agent"|"user"|"system", text, ts?, author?, citations? }[] }',
      bestFor: 'Customer support · sales chat · any agent-to-human dialogue with grounding',
      avoidWhen: 'Output is structured (use TableView)',
    },
  },
  {
    id: 'side-by-side',
    name: 'SideBySideView',
    tag: 'improvement-review · contract',
    meta: 'Two panels rendered next to each other for comparison. Proposal review, before/after diffs, prose-vs-source.',
    render: <SideBySideView data={SIDE_BY_SIDE_DEMO} />,
    spec: {
      inputShape:
        '{ left: { title, body, format?, highlights? }, right: { title, body, format?, highlights? } }',
      bestFor: 'Proposal diffs · before/after improvement view · contract drafting',
      avoidWhen: 'No comparison anchor exists (use a single panel)',
    },
  },
  {
    id: 'document',
    name: 'DocumentReader',
    tag: 'contract',
    meta: 'Long-form document with inline annotations and a comment gutter. The legal / contract review hero.',
    render: <DocumentReader data={DOCUMENT_DEMO} />,
    spec: {
      inputShape:
        '{ section_label?, blocks: { kind, text, spans? }[], annotations: { id, severity, title, body }[] }',
      bestFor: 'Contract review · policy drafting · any reviewer-with-document workflow',
      avoidWhen: 'Document is short (under 200 words) — use SideBySideView',
    },
  },
]

export default async function PrimitivesPage() {
  return (
    <>
      <header className="page-header" style={{ marginBottom: 12 }}>
        <div>
          <h1 className="page-title">Views</h1>
          <p
            className="page-subtitle"
            style={{ maxWidth: 720, lineHeight: 1.5 }}
          >
            How the agent's output is shown. The NL-gen generator picks
            from this set when a workflow is created. Each view is a typed
            contract: skill output → view input → screen the operator
            works in.
          </p>
        </div>
      </header>

      <nav
        style={{
          display: 'flex',
          gap: 8,
          flexWrap: 'wrap',
          padding: '14px 0 22px',
          borderBottom: '1px solid var(--border)',
          marginBottom: 28,
        }}
      >
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            style={{
              fontSize: 12,
              color: 'var(--text-3)',
              padding: '5px 11px',
              border: '1px solid var(--border)',
              borderRadius: 999,
              textDecoration: 'none',
            }}
          >
            {s.name}
          </a>
        ))}
      </nav>

      {SECTIONS.map((s, i) => (
        <section
          key={s.id}
          id={s.id}
          style={{ marginBottom: 36, scrollMarginTop: 20 }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-start',
              gap: 12,
              marginBottom: 14,
              flexWrap: 'wrap',
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 17,
                  fontWeight: 600,
                  color: 'var(--text)',
                  letterSpacing: '-0.01em',
                }}
              >
                {s.name}{' '}
                <code
                  style={{
                    fontFamily:
                      "ui-monospace, 'SF Mono', Menlo, monospace",
                    fontSize: 14,
                    color: 'var(--text-2)',
                    background: 'var(--surface)',
                    padding: '2px 7px',
                    borderRadius: 4,
                    marginLeft: 8,
                  }}
                >
                  v1
                </code>
              </div>
              <div
                style={{
                  fontSize: 12.5,
                  color: 'var(--text-muted)',
                  marginTop: 4,
                  maxWidth: 600,
                  lineHeight: 1.55,
                }}
              >
                {s.meta}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <span
                style={{
                  fontSize: 11,
                  padding: '3px 8px',
                  borderRadius: 999,
                  background: 'var(--accent-muted)',
                  color: 'var(--accent)',
                }}
              >
                {s.tag}
              </span>
            </div>
          </div>
          <div style={{ marginBottom: 14 }}>{s.render}</div>
          <div
            style={{
              background: 'var(--bg)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: '14px 16px',
              boxShadow: 'var(--shadow-sm)',
            }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '140px 1fr',
                gap: '10px 14px',
                fontSize: 12.5,
              }}
            >
              <div style={{ ...SPEC_LABEL_STYLE }}>Input shape</div>
              <div style={SPEC_VALUE_STYLE}>
                <code style={SPEC_CODE_STYLE}>{s.spec.inputShape}</code>
              </div>
              <div style={SPEC_LABEL_STYLE}>Best for</div>
              <div style={SPEC_VALUE_STYLE}>{s.spec.bestFor}</div>
              <div style={SPEC_LABEL_STYLE}>Avoid when</div>
              <div style={SPEC_VALUE_STYLE}>{s.spec.avoidWhen}</div>
            </div>
          </div>
          {i === SECTIONS.length - 1 ? null : (
            <div style={{ height: 1 }} />
          )}
        </section>
      ))}

      <div
        style={{
          background: 'var(--bg)',
          border: '1px dashed var(--border)',
          borderRadius: 8,
          padding: '18px 22px',
          marginTop: 18,
        }}
      >
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--text)',
            marginBottom: 6,
          }}
        >
          Need a view that isn't here?
        </div>
        <div
          style={{
            fontSize: 12.5,
            color: 'var(--text-muted)',
            lineHeight: 1.6,
          }}
        >
          We deliberately keep the set small — every view multiplies the
          surface area to keep stable across themes, eval, and
          accessibility. If you have a workflow that doesn't fit one of
          the nine, file it at{' '}
          <code style={SPEC_CODE_STYLE}>packages/trace-format/SPEC.md</code>{' '}
          with a sketch.
        </div>
      </div>
    </>
  )
}

const SPEC_LABEL_STYLE: React.CSSProperties = {
  color: 'var(--text-muted)',
  fontWeight: 500,
  textTransform: 'uppercase',
  fontSize: 11,
  letterSpacing: '0.05em',
  paddingTop: 1,
}

const SPEC_VALUE_STYLE: React.CSSProperties = {
  color: 'var(--text-2)',
  lineHeight: 1.55,
}

const SPEC_CODE_STYLE: React.CSSProperties = {
  fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace",
  fontSize: 11.5,
  color: 'var(--text)',
  background: 'var(--surface)',
  padding: '1px 5px',
  borderRadius: 3,
}
