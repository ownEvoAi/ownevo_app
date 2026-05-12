import type { WorkflowSummary } from '../../../lib/api'
import { workflowDisplayTitle } from '../../../lib/format'

interface WorkflowsTableProps {
  workflows: WorkflowSummary[]
  wsId: string
}

// Workflow-rows table for the Health page. Visual target:
// www/preview/s26-rk7p3/01-health.html § Workflow rows. Renders one
// row per workflow, link-wrapping the row so clicking anywhere drills
// into the workflow page (slice 6 wires the four workflow routes).
export function WorkflowsTable({ workflows, wsId }: WorkflowsTableProps) {
  if (workflows.length === 0) {
    return (
      <div className="wf-table-empty">
        No workflows in this workspace yet. Run <code>make seed-demo</code> to
        register sample workflows, or click <strong>New workflow</strong> to
        describe one in plain English.
      </div>
    )
  }

  return (
    <div className="wf-table">
      <div className="wf-row head">
        <div>Workflow</div>
        <div>Mode</div>
        <div>Best val_score</div>
        <div>Iterations</div>
        <div>Pending</div>
        <div>Last improved</div>
      </div>
      {workflows.map((w) => (
        <a
          key={w.id}
          className="wf-row"
          href={`/workspaces/${wsId}/workflows/${w.id}`}
          style={{ textDecoration: 'none' }}
        >
          <div className="wf-name">
            {workflowDisplayTitle(w.id, w.description)}
            <span className="wf-name-buyer">{w.id}</span>
          </div>
          <div className="wf-metric">
            <span className="wf-metric-value">{w.mode}</span>
          </div>
          <div className="wf-metric">
            <span className="wf-metric-value">
              {w.best_ever_score !== null ? w.best_ever_score.toFixed(4) : '—'}
            </span>
          </div>
          <div className="wf-metric">
            <span className="wf-metric-value">{w.iteration_count}</span>
            {w.running_iteration_count && w.running_iteration_count > 0 ? (
              <span
                className="wf-inflight"
                title={`${w.running_iteration_count} running`}
              >
                <span className="inflight-dot" />
                {w.running_iteration_count} running
              </span>
            ) : null}
          </div>
          <div className="wf-pending">
            {w.pending_proposals_count > 0 ? (
              <>
                <span className="count">{w.pending_proposals_count}</span> proposals
              </>
            ) : (
              <span style={{ color: 'var(--text-faint)' }}>—</span>
            )}
          </div>
          <div className="wf-last">
            {w.last_improved_at ? formatRelative(w.last_improved_at) : '—'}
          </div>
        </a>
      ))}
    </div>
  )
}

function formatRelative(isoTimestamp: string): string {
  const then = new Date(isoTimestamp).getTime()
  if (Number.isNaN(then)) return isoTimestamp
  const now = Date.now()
  const deltaMin = Math.round((now - then) / 60000)
  if (deltaMin < 1) return 'just now'
  if (deltaMin < 60) return `${deltaMin}m ago`
  const deltaHr = Math.round(deltaMin / 60)
  if (deltaHr < 24) return `${deltaHr}h ago`
  const deltaDay = Math.round(deltaHr / 24)
  if (deltaDay < 30) return `${deltaDay}d ago`
  return new Date(isoTimestamp).toLocaleDateString()
}
