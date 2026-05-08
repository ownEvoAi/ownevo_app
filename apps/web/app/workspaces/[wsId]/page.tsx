import {
  getWorkflowIterations,
  KernelApiError,
  listWorkflows,
  type IterationList,
  type WorkflowList,
} from '../../../lib/api'
import { LiftChart } from './lift-chart'
import { WorkflowsTable } from './workflows-table'

interface PageProps {
  params: Promise<{ wsId: string }>
}

// W7 slice 2 — Workspace Health page.
//
// Hero: LiftChart for the primary workflow (first one returned by
// `GET /api/workflows`, which sorts by `created_at ASC` so demand-
// prediction lands first when the bootstrap seed has run).
//
// Below: workflow-rows table over every workflow in the workspace.
//
// Failure modes: if the kernel API is unreachable (no DB, no API
// running), the page still renders with a clear "no data" message.
// `make web-dev` should work without `make api` per the existing
// W5.5 preview pattern.
export default async function WorkspaceHealthPage({ params }: PageProps) {
  const { wsId } = await params

  const wsLabel = wsId.charAt(0).toUpperCase() + wsId.slice(1)

  let workflows: WorkflowList = { items: [], total: 0 }
  let primaryIterations: IterationList | null = null
  let apiError: string | null = null

  try {
    workflows = await listWorkflows()
    if (workflows.items.length > 0) {
      primaryIterations = await getWorkflowIterations(workflows.items[0].id)
    }
  } catch (err) {
    apiError =
      err instanceof KernelApiError
        ? `Kernel API ${err.status}: ${err.detail}`
        : 'Could not reach the kernel API. Run `make api` to start it.'
  }

  const primary = workflows.items[0]
  const approvedCount = workflows.items.reduce(
    (acc, w) => acc + (w.last_improved_at ? 1 : 0),
    0,
  )
  const pendingCount = workflows.items.reduce(
    (acc, w) => acc + w.pending_proposals_count,
    0,
  )
  const portfolioBest =
    primary && primary.best_ever_score !== null ? primary.best_ever_score : null

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Workflow health</h1>
          <p className="page-subtitle">
            {wsLabel} · {workflows.total} workflow
            {workflows.total === 1 ? '' : 's'}
            {primary ? ` · primary: ${primary.id}` : ''}
          </p>
        </div>
        <div className="page-actions">
          <a href="/workflows/preview" className="btn btn-primary">
            <svg className="btn-icon" viewBox="0 0 16 16">
              <path d="M8 3 L8 13 M3 8 L13 8" />
            </svg>
            New workflow
          </a>
        </div>
      </header>

      {apiError && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            margin: '0 0 24px',
            border: '1px solid var(--banner-border)',
            background: 'var(--banner-bg)',
            color: 'var(--banner-text)',
            borderRadius: 6,
            fontSize: 12.5,
          }}
        >
          <strong>Kernel API not reachable.</strong> {apiError}
        </div>
      )}

      <div className="metrics glance" style={{ marginBottom: 24 }}>
        <div className="metric">
          <div className="metric-label">Active workflows</div>
          <div className="metric-value">{workflows.total}</div>
        </div>
        <div className="metric">
          <div className="metric-label">Pending reviews</div>
          <div className="metric-value">{pendingCount}</div>
        </div>
        <div className="metric">
          <div className="metric-label">Approved (lifetime)</div>
          <div className="metric-value">{approvedCount}</div>
        </div>
        <div className="metric">
          <div className="metric-label">Best val_score</div>
          <div className="metric-value">
            {portfolioBest !== null ? portfolioBest.toFixed(4) : '—'}
          </div>
        </div>
      </div>

      {primary && primaryIterations && (
        <section style={{ marginBottom: 32 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: 12,
            }}
          >
            <h2
              style={{
                fontSize: 13,
                fontWeight: 500,
                color: 'var(--text-2)',
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
              }}
            >
              Lift — {primary.description || primary.id}
            </h2>
            <a
              href={`/workspaces/${wsId}/workflows/${primary.id}`}
              style={{ fontSize: 12, color: 'var(--accent)' }}
            >
              Open workflow →
            </a>
          </div>
          <LiftChart points={primaryIterations.items} workflowId={primary.id} />
        </section>
      )}

      <section>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            marginBottom: 10,
          }}
        >
          <h2
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: 'var(--text-2)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
            }}
          >
            Workflows
          </h2>
        </div>
        <WorkflowsTable workflows={workflows.items} wsId={wsId} />
      </section>
    </>
  )
}
