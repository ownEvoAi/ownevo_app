import Link from 'next/link'
import type { FailureListItem } from '../../../../../../lib/api'

interface TableProps {
 rows: FailureListItem[]
 wsId: string
 wfId: string
}

const SEVERITY_PILL: Record<string, string> = {
 high: 'pill red',
 medium: 'pill amber',
 low: 'pill outline',
}

// 9.2.1 — flat-list view of individual failures across clusters.
// One row per sample trace; sortable by clicking the table header
// is a follow-up. For now rows arrive newest-first (the API orders
// by started_at DESC).
export function FailuresListTable({ rows, wsId, wfId }: TableProps) {
 if (rows.length === 0) {
 return (
 <div className="empty-state">
 No failures match the current filter.
 </div>
 )
 }

 return (
 <div className="failures-list-wrap">
 <table className="failures-list">
 <thead>
 <tr>
 <th>When</th>
 <th>Source</th>
 <th>Severity</th>
 <th>Cluster</th>
 <th>Iteration</th>
 <th>Eval case</th>
 </tr>
 </thead>
 <tbody>
 {rows.map((r) => (
 <tr key={r.trace_id}>
 <td className="failures-list-when">
 {formatDateTime(r.started_at)}
 </td>
 <td>
 <span
 className={`pill source-${
 r.source === 'production' ? 'prod' : 'eval'
 }`}
 >
 {r.source === 'production' ? 'Prod' : 'Eval'}
 </span>
 </td>
 <td>
 <span className={SEVERITY_PILL[r.severity] ?? 'pill'}>
 {r.severity[0].toUpperCase() + r.severity.slice(1)}
 </span>
 </td>
 <td className="failures-list-label">{r.cluster_label}</td>
 <td>
 {r.iteration_index !== null ? (
 <Link
 href={`/workspaces/${wsId}/workflows/${wfId}/iterations/${r.iteration_index}`}
 className="failures-list-link"
 >
 #{r.iteration_index}
 </Link>
 ) : (
 <span className="failures-list-muted">—</span>
 )}
 </td>
 <td>
 {r.eval_case_id ? (
 <code className="failures-list-case">
 {r.eval_case_id.slice(0, 8)}
 </code>
 ) : (
 <span className="failures-list-muted">—</span>
 )}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )
}

function formatDateTime(iso: string | null): string {
 if (!iso) return '—'
 const d = new Date(iso)
 if (Number.isNaN(d.getTime() )) return iso
 const date = d.toISOString().slice(0, 10)
 const time = d.toISOString().slice(11, 16)
 return `${date} ${time}`
}
