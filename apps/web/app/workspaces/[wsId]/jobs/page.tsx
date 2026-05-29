import type { CSSProperties } from 'react'
import {
 kernelError,
 listJobs,
 type JobList,
 type JobSummary,
} from '@/lib/api'
import { formatDateTime, relativeTime } from '@/lib/format'

interface PageProps {
 params: Promise<{ wsId: string }>
}

// Status -> badge colour. queued = waiting, running = in-flight,
// succeeded = done, failed = retries exhausted (the one to watch).
const STATUS_COLOR: Record<string, string> = {
 queued: '#6b7280',
 running: '#2563eb',
 succeeded: '#16a34a',
 failed: '#dc2626',
}

// Read-only view of the durable job queue. Trigger-fired improvement-loop
// iterations are enqueued here and drained by a background worker; this page
// shows the current depth (queued / running / failed) and the most recent
// jobs so an operator can see the queue's health without the database.
export default async function WorkspaceJobsPage({ params }: PageProps) {
 await params

 let jobs: JobList = { items: [], counts: {} }
 let apiError: { title: string; detail: string } | null = null
 try {
 jobs = await listJobs()
 } catch (err) {
 apiError = kernelError(err)
 }

 const counts = jobs.counts
 const depth = (s: string) => counts[s] ?? 0

 return (
 <>
 <header className="page-header" style={{ marginBottom: 12 }}>
 <div>
 <h1 className="page-title">Jobs</h1>
 <p className="page-subtitle">
 Durable background-job queue · newest first ·{' '}
 {jobs.items.length} shown
 </p>
 </div>
 <div style={{ display: 'flex', gap: 8 }}>
 <CountBadge label="queued" value={depth('queued')} />
 <CountBadge label="running" value={depth('running')} />
 <CountBadge label="failed" value={depth('failed')} />
 </div>
 </header>

 {apiError && (
 <div role="alert" className="api-banner">
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )}

 {jobs.items.length === 0 && !apiError ? (
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
 No jobs yet. Background jobs are enqueued when a trigger fires a
 workflow iteration; this list fills in once a trigger runs.
 </div>
 ) : (
 <table
 style={{
 width: '100%',
 borderCollapse: 'collapse',
 fontSize: 13,
 }}
 >
 <thead>
 <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
 <th style={th}>Job</th>
 <th style={th}>Workflow</th>
 <th style={th}>Status</th>
 <th style={th}>Attempts</th>
 <th style={th}>Created</th>
 <th style={th}>Last error</th>
 </tr>
 </thead>
 <tbody>
 {jobs.items.map((job) => (
 <JobRow key={job.id} job={job} />
 ))}
 </tbody>
 </table>
 )}
 </>
 )
}

const th: CSSProperties = {
 padding: '6px 10px',
 borderBottom: '1px solid var(--border)',
 fontWeight: 500,
}

const td: CSSProperties = {
 padding: '8px 10px',
 borderBottom: '1px solid var(--border)',
 verticalAlign: 'top',
}

function CountBadge({ label, value }: { label: string; value: number }) {
 const color = STATUS_COLOR[label] ?? 'var(--text-muted)'
 return (
 <span
 style={{
 display: 'inline-flex',
 alignItems: 'center',
 gap: 6,
 fontSize: 12,
 color: 'var(--text-muted)',
 }}
 >
 <span
 style={{
 width: 8,
 height: 8,
 borderRadius: '50%',
 background: color,
 display: 'inline-block',
 }}
 />
 {value} {label}
 </span>
 )
}

function JobRow({ job }: { job: JobSummary }) {
 const color = STATUS_COLOR[job.status] ?? 'var(--text-muted)'
 return (
 <tr>
 <td style={td}>
 <code style={{ fontSize: 11.5 }}>{job.kind}</code>
 <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>
 {job.id.slice(0, 8)}
 </div>
 </td>
 <td style={td}>
 {job.workflow_id ? (
 <code style={{ fontSize: 11.5 }}>{job.workflow_id}</code>
 ) : (
 <span style={{ color: 'var(--text-muted)' }}>(none)</span>
 )}
 </td>
 <td style={td}>
 <span style={{ color, fontWeight: 500 }}>{job.status}</span>
 </td>
 <td style={td}>
 {job.attempts}/{job.max_attempts}
 </td>
 <td style={td} title={formatDateTime(job.created_at)}>
 {relativeTime(job.created_at)}
 </td>
 <td style={{ ...td, color: 'var(--text-muted)', maxWidth: 280 }}>
 {job.last_error ? (
 <span title={job.last_error}>
 {job.last_error.length > 80
 ? `${job.last_error.slice(0, 80)}…`
 : job.last_error}
 </span>
 ) : (
 '—'
 )}
 </td>
 </tr>
 )
}
