import Link from 'next/link'
import { notFound } from 'next/navigation'
import { getTrace, KernelApiError, type TraceDetail } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { TraceStep } from './trace-step'

interface PageProps {
  params: Promise<{ wsId: string; traceId: string }>
}

// W7 slice 8 (7.1.9) — per-trace step inspection page.
//
// Visual target: www/preview/s26-rk7p3/15-traces.html § .trace-detail
// (right pane). Closes the LangSmith / LangFuse parallel for the
// workspace UI: every AgentEvent variant in packages/trace-format/SPEC.md
// renders chronologically with offset-from-start timing + expandable
// input/output. Pure server component — zero client JS, the native
// <details> element drives the per-step expand.
export default async function TraceDetailPage({ params }: PageProps) {
  const { wsId, traceId } = await params

  let trace: TraceDetail
  try {
    trace = await getTrace(traceId)
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
    throw err
  }

  const startedAtMs = new Date(trace.started_at).getTime()
  const durationMs =
    trace.ended_at !== null
      ? new Date(trace.ended_at).getTime() - startedAtMs
      : null
  const wfHref =
    trace.workflow_id !== null
      ? `/workspaces/${wsId}/workflows/${trace.workflow_id}/traces`
      : `/workspaces/${wsId}`

  return (
    <div>
      <nav className="crumb-row">
        <Link href={`/workspaces/${wsId}`}>Workspace</Link>
        {trace.workflow_id && (
          <>
            <span className="sep">/</span>
            <Link href={wfHref}>{trace.workflow_id}</Link>
          </>
        )}
        <span className="sep">/</span>
        <span>Trace {trace.id.slice(0, 8)}</span>
      </nav>

      <div className="trace-detail-head" style={{ marginTop: 12 }}>
        <div className="trace-detail-head-row">
          <div>
            <div className="trace-detail-title">trace · {trace.id}</div>
            <div className="trace-detail-summary">
              {trace.skill_id ? `${trace.skill_id} v${trace.skill_version_seq}` : 'Standalone trace'}
            </div>
            <div className="trace-detail-meta">
              <span>
                <strong>Started</strong> {formatDateTime(trace.started_at)}
              </span>
              <span>
                <strong>Duration</strong>{' '}
                {durationMs !== null ? formatDuration(durationMs) : 'in flight'}
              </span>
              <span>
                <strong>Events</strong> {trace.events.length}
              </span>
              {trace.iteration_index !== null && (
                <span>
                  <strong>Iteration</strong> #{trace.iteration_index}
                </span>
              )}
            </div>
          </div>
          {trace.token_usage && Object.keys(trace.token_usage).length > 0 && (
            <TokenUsage usage={trace.token_usage} />
          )}
        </div>
      </div>

      <div className="timeline">
        {trace.events.length === 0 ? (
          <div
            style={{
              padding: 32,
              textAlign: 'center',
              color: 'var(--text-muted)',
              fontSize: 13,
            }}
          >
            No events recorded on this trace.
          </div>
        ) : (
          trace.events.map((event) => (
            <TraceStep key={event.event_id} event={event} startedAtMs={startedAtMs} />
          ))
        )}
      </div>
    </div>
  )
}

function TokenUsage({ usage }: { usage: Record<string, unknown> }) {
  return (
    <div className="sidebar-card" style={{ minWidth: 180 }}>
      <div className="sidebar-title">Token usage</div>
      <div className="impact-grid">
        {Object.entries(usage).map(([k, v]) => (
          <div key={k} className="impact-cell">
            <div className="impact-label">{k}</div>
            <div className="impact-value">{String(v)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}m`
}
