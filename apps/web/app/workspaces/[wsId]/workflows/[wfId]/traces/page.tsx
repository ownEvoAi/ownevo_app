import Link from 'next/link'
import {
  getWorkflowTraces,
  kernelError,
  KernelApiError,
  type TraceList,
  type TraceSummary,
} from '@/lib/api'
import { formatDateTime, relativeTime } from '@/lib/format'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// W7 slice 8 (7.1.9) — per-workflow trace list. Sits behind the
// "Traces" tab on the workflow detail shell. Click-through opens the
// per-trace step inspection page at
// /workspaces/[wsId]/traces/[traceId].
export default async function WorkflowTracesPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let traces: TraceList = { workflow_id: wfId, items: [] }
  let apiError: { title: string; detail: string } | null = null
  let notFound = false

  try {
    traces = await getWorkflowTraces(wfId)
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound = true
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

      {notFound && (
        <div role="alert" className="api-banner">
          <strong>Workflow not found.</strong> No workflow with id{' '}
          <code>{wfId}</code> in this workspace.
        </div>
      )}

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: 12,
        }}
      >
        <h2 className="section-title">Traces · {traces.items.length}</h2>
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
          Click a row to open the step timeline.
        </span>
      </div>

      {traces.items.length === 0 && !apiError && !notFound ? (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 32,
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: 13,
          }}
        >
          No traces yet for <code>{wfId}</code>. Traces are written
          on every gate run; trigger one with{' '}
          <code>make m5-run</code>.
        </div>
      ) : (
        <div className="trace-list">
          <div className="trace-list-head">
            <span>Trace</span>
            <span>Events · started</span>
          </div>
          {traces.items.map((t) => (
            <TraceRow key={t.id} wsId={wsId} trace={t} />
          ))}
        </div>
      )}
    </>
  )
}

function TraceRow({ wsId, trace }: { wsId: string; trace: TraceSummary }) {
  const idShort = trace.id.slice(0, 8)
  const kindEntries = Object.entries(trace.kind_counts).sort(
    (a, b) => b[1] - a[1],
  )
  const startedDur =
    trace.ended_at !== null
      ? new Date(trace.ended_at).getTime() - new Date(trace.started_at).getTime()
      : null
  return (
    <Link
      href={`/workspaces/${wsId}/traces/${trace.id}`}
      className="trace-row"
      style={{ textDecoration: 'none', display: 'block', color: 'inherit' }}
    >
      <div className="trace-id">trace · {idShort}</div>
      <div className="trace-summary">
        {trace.iteration_index !== null
          ? `Iteration #${trace.iteration_index}`
          : 'Standalone trace'}
      </div>
      <div className="trace-meta">
        <span>{trace.event_count} events</span>
        <span>·</span>
        <span title={formatDateTime(trace.started_at)}>
          {relativeTime(trace.started_at)}
        </span>
        {startedDur !== null && (
          <>
            <span>·</span>
            <span>{startedDur < 1000 ? `${startedDur}ms` : `${(startedDur / 1000).toFixed(1)}s`}</span>
          </>
        )}
        {kindEntries.length > 0 && (
          <>
            <span>·</span>
            <span>
              {kindEntries
                .slice(0, 3)
                .map(([k, c]) => `${k}×${c}`)
                .join(' ')}
            </span>
          </>
        )}
      </div>
    </Link>
  )
}
