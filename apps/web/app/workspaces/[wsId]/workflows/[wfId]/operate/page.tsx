import Link from 'next/link'
import {
  getWorkflowAnatomy,
  getWorkflowIterations,
  kernelError,
  KernelApiError,
  listProposals,
  listWorkflowEvalCases,
  type EvalCaseSummary,
  type IterationPoint,
  type ProposalSummary,
  type WorkflowSpecShape,
} from '@/lib/api'
import { formatDateTime, formatScore, relativeTime } from '@/lib/format'
import { MetricCards } from '@/app/components/primitives/metric-cards'
import { TimeSeriesChart } from '@/app/components/primitives/time-series-chart'
import { resolveTabPrimitives } from '@/lib/primitive-data-resolver'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Operate tab — mock parity with www/preview/s26-rk7p3/06-workflow-operate.html
// (also 09 support, 10 contract, 10b labour). Renders the primitives
// the spec declared on its "operate" tab. The layer-D resolver maps
// MetricCards + TimeSeriesChart to real iteration-derived data; other
// primitives (TableView / KanbanBoard / ScheduleGrid / ConversationView /
// DocumentReader / etc.) render an empty placeholder until the agent
// emits per-case output rich enough to populate them.
export default async function WorkflowOperatePage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let spec: WorkflowSpecShape | null = null
  let description: string | null = null
  let iterations: IterationPoint[] = []
  let evalCases: EvalCaseSummary[] = []
  let proposals: ProposalSummary[] = []
  let apiError: { title: string; detail: string } | null = null

  try {
    const [anatomy, iterList, evalList, propList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowIterations(wfId),
      listWorkflowEvalCases(wfId),
      listProposals({ workflow_id: wfId, limit: 100 }),
    ])
    spec = anatomy.spec
    description = anatomy.description
    iterations = iterList.items
    evalCases = evalList.items
    proposals = propList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  if (apiError) {
    return (
      <div role="alert" className="api-banner">
        <strong>{apiError.title}</strong> {apiError.detail}
      </div>
    )
  }

  // Find the "Operate" tab in the spec UI plan. Spec tabs vary by
  // workflow (`Overview` / `Operate` / `Investigate` / etc.); we look
  // for "operate" by name and fall back to the SECOND tab (most specs
  // put Overview at index 0, the operate-shaped view at index 1).
  const tabs = spec?.ui?.tabs ?? []
  const operateTab =
    tabs.find((t) => (t.name ?? '').toLowerCase() === 'operate') ?? tabs[1]

  const primitives = operateTab
    ? resolveTabPrimitives({ spec, iterations, evalCases, proposals }, operateTab.name ?? 'operate') ??
      []
    : []

  const resolved = primitives.filter((p) => p.kind !== 'empty')
  const unresolvedTypes = primitives
    .filter((p): p is Extract<typeof primitives[number], { kind: 'empty' }> => p.kind === 'empty')
    .map((p) => p.primitiveType)

  const latestIter = iterations.length > 0 ? iterations[iterations.length - 1] : null
  const pendingProposals = proposals.filter(
    (p) => p.state === 'gate-passed' || p.state === 'pending',
  )

  const operatorHref = `/operator/${wfId}?ws=${encodeURIComponent(wsId)}`

  return (
    <>
      {/* Prominent CTA to the dedicated operator shell — same data,
          no improvement-loop chrome. The shell is what a non-developer
          domain expert would actually use day-to-day; the Operate tab
          is the in-context preview. */}
      <section className="operate-agent-cta">
        <div className="operate-agent-cta-text">
          <div className="operate-agent-cta-title">Agent-only view</div>
          <div className="operate-agent-cta-sub">
            What the agent produces, stripped of eval-loop chrome.
            Bookmark this for the domain expert who reviews output —
            no developer concepts in sight.
          </div>
        </div>
        <Link href={operatorHref} className="btn btn-primary operate-agent-cta-btn">
          Open agent-only view ↗
        </Link>
      </section>

      <section className="operate-status">
        <div className="operate-status-pill">
          <span
            className={`operate-status-dot ${iterations.length > 0 ? 'live' : 'idle'}`}
          />
          <strong>{iterations.length > 0 ? 'Active' : 'Idle'}</strong>
          {latestIter !== null && latestIter.ended_at !== null ? (
            <span style={{ color: 'var(--text-muted)' }}>
              · last run {relativeTime(latestIter.ended_at)}
            </span>
          ) : null}
        </div>
        <div className="operate-status-cells">
          <div className="operate-status-cell">
            <div className="operate-status-label">Current val_score</div>
            <div className="operate-status-value">
              {latestIter?.val_score !== null && latestIter?.val_score !== undefined
                ? formatScore(latestIter.val_score)
                : '—'}
            </div>
          </div>
          <div className="operate-status-cell">
            <div className="operate-status-label">Iterations</div>
            <div className="operate-status-value">{iterations.length}</div>
          </div>
          <div className="operate-status-cell">
            <div className="operate-status-label">Eval cases</div>
            <div className="operate-status-value">{evalCases.length}</div>
          </div>
          <div className="operate-status-cell">
            <div className="operate-status-label">Pending review</div>
            <div className="operate-status-value">
              {pendingProposals.length > 0 ? (
                <Link
                  href={`/workspaces/${wsId}/workflows/${wfId}/proposals`}
                  style={{ color: 'var(--accent)' }}
                >
                  {pendingProposals.length}
                </Link>
              ) : (
                <span style={{ color: 'var(--text-muted)' }}>0</span>
              )}
            </div>
          </div>
        </div>
      </section>

      {description ? (
        <p
          style={{
            fontSize: 12.5,
            color: 'var(--text-muted)',
            marginBottom: 16,
            lineHeight: 1.5,
          }}
        >
          {description}
        </p>
      ) : null}

      {!operateTab && (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 20,
            color: 'var(--text-muted)',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          This workflow&rsquo;s spec doesn&rsquo;t declare an{' '}
          <code>Operate</code> tab yet — primitives compose from spec
          UI layout. The recent runs below + the agent-only view above
          still give you the live agent surface.
        </div>
      )}

      {operateTab && primitives.length === 0 && (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 28,
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: 13,
          }}
        >
          The <code>{operateTab.name}</code> tab has no primitives declared.
        </div>
      )}

      {resolved.length > 0 && (
        <section className="overview-primitives" style={{ marginTop: 12 }}>
          {resolved.map((p, i) => {
            if (p.kind === 'MetricCards') return <MetricCards key={i} data={p.data} />
            if (p.kind === 'TimeSeriesChart')
              return <TimeSeriesChart key={i} data={p.data} />
            return null
          })}
        </section>
      )}

      {unresolvedTypes.length > 0 && (
        <p className="overview-primitives-unresolved" style={{ marginTop: 14 }}>
          Spec declares{' '}
          <strong>{Array.from(new Set(unresolvedTypes)).join(', ')}</strong> on
          the {operateTab?.name ?? 'Operate'} tab — those primitives need
          richer per-case agent output than the current loop emits. Their
          placeholders fill in once the iteration runner captures structured
          predictions beyond <code>bool</code>.
        </p>
      )}

      {iterations.length > 0 && (
        <section style={{ marginTop: 18 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: 8,
            }}
          >
            <h2 className="section-title" style={{ margin: 0 }}>
              Recent runs
            </h2>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {iterations.length} total · click a row for per-case detail
            </span>
          </div>
          <div className="iter-overview-list">
            <div className="iter-overview-row iter-overview-head">
              <span>Iter</span>
              <span>val_score</span>
              <span>Best ever</span>
              <span>State</span>
              <span>Approved?</span>
              <span>Ended</span>
            </div>
            {[...iterations].reverse().slice(0, 10).map((it) => (
              <Link
                key={it.iteration_index}
                href={`/workspaces/${wsId}/workflows/${wfId}/iterations/${it.iteration_index}`}
                className="iter-overview-row"
              >
                <span className="iter-overview-idx">#{it.iteration_index}</span>
                <span className="iter-overview-num">
                  {it.val_score !== null ? it.val_score.toFixed(3) : '—'}
                </span>
                <span className="iter-overview-num">
                  {it.best_ever_score_after !== null
                    ? it.best_ever_score_after.toFixed(3)
                    : '—'}
                </span>
                <span className="iter-overview-state">{it.state}</span>
                <span className="iter-overview-approved">
                  {it.has_approved_proposal ? '✓' : ''}
                </span>
                <span className="iter-overview-when">
                  {it.ended_at
                    ? formatDateTime(it.ended_at).slice(0, 16)
                    : '—'}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {iterations.length === 0 && (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 28,
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: 13,
            marginTop: 14,
          }}
        >
          The agent hasn&rsquo;t run yet on this workflow. Trigger the
          first iteration from the Overview tab.
        </div>
      )}
    </>
  )
}
