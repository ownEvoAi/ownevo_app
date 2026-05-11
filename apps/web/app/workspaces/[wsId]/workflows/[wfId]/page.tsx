import {
  getWorkflowAnatomy,
  getWorkflowSkills,
  kernelError,
  KernelApiError,
  type SkillSummary,
  type WorkflowSpecShape,
} from '@/lib/api'
import { AgentAnatomy } from '@/app/components/agent-anatomy'
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
import { getWorkflowOverviewPrimitives } from '@/lib/primitives-mock-data'
import { getMock } from './mocks'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

const ACTIVITY_TONE: Record<string, string> = {
  approval: 'var(--green)',
  cluster: 'var(--amber)',
  regression: 'var(--red)',
  escalation: 'var(--accent)',
}

// Map the semantic delta tone (positive/negative/neutral business
// outcome) onto the existing primitives.css colour classes.
const TONE_CLASS: Record<'positive' | 'negative' | 'neutral', string> = {
  positive: 'up',
  negative: 'down',
  neutral: 'flat',
}

// Workflow Overview tab.
//
// For wfId in {labour, contract, support}: renders mock metrics +
// recent-activity feed from mocks.ts. For demand-prediction (live):
// surfaces a placeholder pointing at Failures + Audit until the
// W8.1.1 wiring lands.
//
// W7 slice 11 (7.1.12) — both branches mount the AgentAnatomy pane
// above the fold so reviewers see "what the agent CAN do" before
// scrolling to metrics. Mock surfaces feed hand-authored
// skills+spec from mocks.ts; live surfaces fetch from the kernel.
export default async function WorkflowOverviewPage({ params }: PageProps) {
  const { wsId, wfId } = await params
  const mock = getMock(wfId)

  if (mock) {
    return (
      <>
        <AgentAnatomy
          wsId={wsId}
          workflowId={null}
          skills={mock.anatomy.skills}
          spec={mock.anatomy.spec}
        />

        <div className="metrics glance" style={{ marginBottom: 24, marginTop: 24 }}>
          {mock.metrics.map((m) => (
            <div key={m.label} className="metric">
              <div className="metric-label">{m.label}</div>
              <div className="metric-value">{m.value}</div>
              {m.delta && (
                <div className={`metric-delta ${TONE_CLASS[m.delta.tone]}`}>{m.delta.text}</div>
              )}
            </div>
          ))}
        </div>

        <section style={{ marginBottom: 24 }}>
          <h2
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: 'var(--text-2)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: 10,
            }}
          >
            Recent activity
          </h2>
          <div className="activity">
            {mock.recentActivity.map((a, i) => (
              <div key={i} className="activity-item">
                <div
                  className="activity-dot"
                  style={{ background: ACTIVITY_TONE[a.kind] ?? 'var(--text-faint)' }}
                />
                <div className="activity-body">
                  {a.body}
                  <div className="activity-meta">{a.when}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <WorkflowPrimitives wfId={wfId} />

        <section
          style={{
            marginTop: 24,
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 20,
            color: 'var(--text-muted)',
            fontSize: 13,
            lineHeight: 1.55,
          }}
        >
          This workflow is positioning copy. The four glance metrics, the activity feed,
          and the failure clusters under the Failures tab are hand-authored. The
          improvement loop, eval-case promotion, gate, and audit chain are the same as
          for any live workflow — they just don&rsquo;t run on this dataset yet.
        </section>
      </>
    )
  }

  // Live workflow (demand-prediction or any other backend-registered id).
  let skills: SkillSummary[] = []
  let spec: WorkflowSpecShape | null = null
  let apiError: { title: string; detail: string } | null = null
  try {
    const [anatomy, skillList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowSkills(wfId),
    ])
    spec = anatomy.spec
    skills = skillList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  return (
    <>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      <AgentAnatomy wsId={wsId} workflowId={wfId} skills={skills} spec={spec} />

      <WorkflowPrimitives wfId={wfId} />
    </>
  )
}

// Render the per-workflow primitive bundle from the mock resolver
// (Track 0 layer C). Phase-2 resolver (TODO-35) replaces this with
// live agent-output data; until then, the curated payloads keep the
// Overview page looking live. Returns null if the workflow has no
// curated primitives (e.g. an NL-gen'd workflow that hasn't been
// hand-mocked yet) — caller decides what to render in its absence.
function WorkflowPrimitives({ wfId }: { wfId: string }) {
  const p = getWorkflowOverviewPrimitives(wfId)
  if (!p) {
    return (
      <section
        style={{
          marginTop: 24,
          background: 'var(--bg)',
          border: '1px dashed var(--border)',
          borderRadius: 8,
          padding: 20,
          color: 'var(--text-muted)',
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        No render primitives configured for this workflow yet. The Phase-2
        resolver (TODO-35) will compose them from agent output.
      </section>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, marginTop: 24 }}>
      <div
        style={{
          fontSize: 11,
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          fontWeight: 500,
        }}
      >
        Operator view · preview data
      </div>
      {p.metricCards ? <MetricCards data={p.metricCards} /> : null}
      {p.timeSeriesChart ? <TimeSeriesChart data={p.timeSeriesChart} /> : null}
      {p.tableView ? <TableView data={p.tableView} /> : null}
      {p.scheduleGrid ? <ScheduleGrid data={p.scheduleGrid} /> : null}
      {p.alertList ? <AlertList data={p.alertList} /> : null}
      {p.kanbanBoard ? <KanbanBoard data={p.kanbanBoard} /> : null}
      {p.conversationView ? <ConversationView data={p.conversationView} /> : null}
      {p.sideBySideView ? <SideBySideView data={p.sideBySideView} /> : null}
      {p.documentReader ? <DocumentReader data={p.documentReader} /> : null}
    </div>
  )
}
