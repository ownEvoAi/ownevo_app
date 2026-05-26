import Link from 'next/link'
import {
 kernelError,
 listAllTraces,
 type TraceList,
 type TraceSummary,
} from '@/lib/api'
import { formatDateTime, relativeTime } from '@/lib/format'

interface PageProps {
 params: Promise<{ wsId: string }>
}

// Workspace-scoped traces list — mock parity: s26-rk7p3/15-traces.html.
// Same row shape as the per-workflow Traces tab; the difference is
// the workflow_id column on each row (since the list spans every
// workflow). Capped at 500 rows server-side; keyset pagination is the
// follow-up if real customer trace volume pushes past that limit.
export default async function WorkspaceTracesPage({ params }: PageProps) {
 const { wsId } = await params

 let traces: TraceList = { workflow_id: '', items: [] }
 let apiError: { title: string; detail: string } | null = null
 try {
 traces = await listAllTraces } catch (err) {
 apiError = kernelError(err)
 }

 return (
 <>
 <header className="page-header" style={{ marginBottom: 12 }}>
 <div>
 <h1 className="page-title">Traces</h1>
 <p className="page-subtitle">
 Every trace across every workflow · newest first ·{' '}
 {traces.items.length} shown
 </p>
 </div>
 </header>

 {apiError && (
 <div role="alert" className="api-banner">
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )}

 {traces.items.length === 0 && !apiError ? (
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
 No traces yet. Each iteration the agent runs writes one trace
 per eval case — kick off a Run iteration on any workflow&rsquo;s
 Overview tab to populate this list.
 </div>
 ) : (
 <div className="trace-list">
 <div className="trace-list-head trace-list-head-ws">
 <span>Trace</span>
 <span>Workflow</span>
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
 ? new Date(trace.ended_at).getTime -
 new Date(trace.started_at).getTime : null
 return (
 <Link
 href={`/workspaces/${wsId}/traces/${trace.id}`}
 className="trace-row trace-row-ws"
 style={{ textDecoration: 'none', display: 'block', color: 'inherit' }}
 >
 <div className="trace-id">trace · {idShort}</div>
 <div className="trace-workflow">
 {trace.workflow_id ? (
 <code style={{ fontSize: 11.5 }}>{trace.workflow_id}</code>
 ) : (
 <span style={{ color: 'var(--text-muted)' }}>(none)</span>
 )}
 </div>
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
 <span>
 {startedDur < 1000
 ? `${startedDur}ms`
 : `${(startedDur / 1000).toFixed(1)}s`}
 </span>
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
