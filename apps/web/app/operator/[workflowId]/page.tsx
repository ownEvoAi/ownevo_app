import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  getWorkflowAnatomy,
  getWorkflowIterations,
  KernelApiError,
  listProposals,
  listWorkflowEvalCases,
  listWorkflows,
  type EvalCaseSummary,
  type IterationPoint,
  type ProposalSummary,
  type WorkflowSpecShape,
  type WorkflowSummary,
} from '@/lib/api'
import { formatDateTime, formatScore, relativeTime, workflowDisplayTitle } from '@/lib/format'
import { MetricCards } from '@/app/components/primitives/metric-cards'
import { TimeSeriesChart } from '@/app/components/primitives/time-series-chart'
import { resolveTabPrimitives, resolvePrimitives } from '@/lib/primitive-data-resolver'
import { WorkflowSwitcher } from './workflow-switcher'

interface PageProps {
  params: Promise<{ workflowId: string }>
  searchParams: Promise<{ ws?: string }>
}

// Operator shell — mock parity: s26-rk7p3/28-operator-support, 29-contract,
// 30-demand, 31-labour. One route, four personas (the workflow_id picks
// which one). The shell renders what the agent has produced for review,
// stripped of the improvement-loop chrome.
//
// What's wired today: the Operate spec's primitives (MetricCards,
// TimeSeriesChart) plus a recent-runs table.
//
// What's NOT wired (would land when per-case structured output is
// captured, PLAN row 8.4.6 layer-D resolver expansion): the
// SKU/account/case-level recommendation table the mocks show. Each
// agent recommendation row needs the agent to emit structured output
// per case beyond the bool prediction the iteration runner sees today.
// We surface the gap as a callout instead of mocking the rows.
export default async function OperatorPage({ params, searchParams }: PageProps) {
  const { workflowId } = await params
  const { ws } = await searchParams
  const wsId = ws || 'acme'

  let spec: WorkflowSpecShape | null = null
  let description: string | null = null
  let workflowName: string = workflowId
  let iterations: IterationPoint[] = []
  let evalCases: EvalCaseSummary[] = []
  let proposals: ProposalSummary[] = []
  let allWorkflows: WorkflowSummary[] = []

  try {
    const [anatomy, iterList, evalList, propList, wfList] = await Promise.all([
      getWorkflowAnatomy(workflowId),
      getWorkflowIterations(workflowId),
      listWorkflowEvalCases(workflowId),
      listProposals({ workflow_id: workflowId, limit: 100 }),
      listWorkflows(),
    ])
    spec = anatomy.spec
    description = anatomy.description
    workflowName = workflowDisplayTitle(anatomy.id, anatomy.description, 60)
    iterations = iterList.items
    evalCases = evalList.items
    proposals = propList.items
    allWorkflows = wfList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
    throw err
  }

  const tabs = spec?.ui?.tabs ?? []
  const operateTab =
    tabs.find((t) => (t.name ?? '').toLowerCase() === 'operate') ?? tabs[1]
  const primitives = operateTab
    ? resolveTabPrimitives(
        { spec, iterations, evalCases, proposals },
        operateTab.name ?? 'operate',
      ) ?? []
    : resolvePrimitives({ spec, iterations, evalCases, proposals })

  const resolved = primitives.filter((p) => p.kind !== 'empty')
  const unresolvedTypes = primitives
    .filter((p): p is Extract<typeof primitives[number], { kind: 'empty' }> => p.kind === 'empty')
    .map((p) => p.primitiveType)

  const latest = iterations.length > 0 ? iterations[iterations.length - 1] : null
  const pendingProposals = proposals.filter(
    (p) => p.state === 'gate-passed' || p.state === 'pending',
  )

  return (
    <>
      <div className="op-bar">
        <Link href={`/workspaces/${wsId}`} className="op-bar-brand">
          <svg className="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 1.75 L20.25 4.75 V12 C20.25 17 16.5 20.75 12 22.25 C7.5 20.75 3.75 17 3.75 12 V4.75 Z"
              fill="#3b82f6"
            />
            <circle cx="12" cy="12.5" r="3.2" stroke="#07090e" strokeWidth={2} />
            <path d="M9.6 7 L12 4.5 L14.4 7 Z" fill="#07090e" />
          </svg>
          <span className="op-bar-brand-label">ownEvo</span>
        </Link>
        <span className="op-bar-sep">/</span>
        <WorkflowSwitcher
          workflows={allWorkflows}
          current={workflowId}
          wsId={wsId}
          currentLabel={workflowName}
        />
        <span className="op-bar-spacer" />
        <Link
          href={`/workspaces/${wsId}/workflows/${workflowId}`}
          className="op-bar-action"
          style={{ color: 'var(--accent)' }}
        >
          view as owner →
        </Link>
      </div>

      <main className="op-main">
        <div className="op-shell-banner">
          <strong>Operator view.</strong> Domain-expert review surface — no
          improvement-loop chrome. Below: what the agent has produced for
          this workflow.{' '}
          <Link
            href={`/workspaces/${wsId}/workflows/${workflowId}`}
            style={{ color: 'var(--accent)' }}
          >
            Open the AgentOS view ↗
          </Link>{' '}
          to see eval cases, failures, proposals, and the lift curve.
        </div>

        <div className="op-stats">
          <div className="op-stat">
            <div className="op-stat-label">Status</div>
            <div className="op-stat-val">
              {iterations.length > 0 ? 'Active' : 'Idle'}
            </div>
            <div className="op-stat-meta">
              {latest?.ended_at ? (
                <>last run {relativeTime(latest.ended_at)}</>
              ) : (
                'no runs yet'
              )}
            </div>
          </div>
          <div className="op-stat">
            <div className="op-stat-label">Current accuracy</div>
            <div className="op-stat-val">
              {latest?.val_score !== null && latest?.val_score !== undefined
                ? formatScore(latest.val_score)
                : '—'}
            </div>
            <div className="op-stat-meta">val_score</div>
          </div>
          <div className="op-stat">
            <div className="op-stat-label">Eval suite</div>
            <div className="op-stat-val">{evalCases.length}</div>
            <div className="op-stat-meta">cases under regression gate</div>
          </div>
          <div className="op-stat">
            <div className="op-stat-label">Pending your review</div>
            <div className="op-stat-val">
              {pendingProposals.length > 0 ? (
                <Link
                  href={`/workspaces/${wsId}/workflows/${workflowId}/proposals`}
                  style={{ color: 'var(--accent)' }}
                >
                  {pendingProposals.length}
                </Link>
              ) : (
                <span style={{ color: 'var(--text-muted)' }}>0</span>
              )}
            </div>
            <div className="op-stat-meta">approve to deploy</div>
          </div>
        </div>

        {description ? (
          <p className="op-workflow-blurb">{description}</p>
        ) : null}

        {resolved.length > 0 && (
          <section className="overview-primitives" style={{ marginTop: 14 }}>
            {resolved.map((p, i) => {
              if (p.kind === 'MetricCards') return <MetricCards key={i} data={p.data} />
              if (p.kind === 'TimeSeriesChart')
                return <TimeSeriesChart key={i} data={p.data} />
              return null
            })}
          </section>
        )}

        {unresolvedTypes.length > 0 && (
          <div className="op-shell-coming">
            <strong>Planned surfaces.</strong> Spec declares{' '}
            <code>{Array.from(new Set(unresolvedTypes)).join(', ')}</code> for
            this workflow — the per-row recommendation table, alerts list,
            and so on. They light up here when the iteration runner starts
            capturing structured per-case agent output (not just the bool
            prediction the regression gate scores).
          </div>
        )}

        {iterations.length > 0 && (
          <section style={{ marginTop: 18 }}>
            <h2 className="section-title" style={{ marginBottom: 8 }}>
              Recent runs
            </h2>
            <div className="iter-overview-list">
              <div className="iter-overview-row iter-overview-head">
                <span>Iter</span>
                <span>val_score</span>
                <span>Best ever</span>
                <span>State</span>
                <span>Approved?</span>
                <span>Ended</span>
              </div>
              {[...iterations].reverse().slice(0, 12).map((it) => (
                <Link
                  key={it.iteration_index}
                  href={`/workspaces/${wsId}/workflows/${workflowId}/iterations/${it.iteration_index}`}
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
      </main>
    </>
  )
}
