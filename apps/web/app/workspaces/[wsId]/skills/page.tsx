import Link from 'next/link'
import {
 kernelError,
 listSkills,
 listWorkflows,
 type SkillSummary,
 type WorkflowSummary,
} from '@/lib/api'
import { relativeTime, workflowDisplayTitle } from '@/lib/format'

interface PageProps {
 params: Promise<{ wsId: string }>
 searchParams: Promise<{ workflow?: string }>
}

interface SkillRow {
 id: string
 kind: SkillSummary['kind']
 capability_tags: string[]
 workflow_id: string | null
 workflow_title: string
 head_version_seq: number | null
 head_created_at: string | null
}

// Skills library — workspace-scoped list of every registered skill,
// joined against workflows for the parent-workflow column.
export default async function SkillsLibraryPage({
 params,
 searchParams,
}: PageProps) {
 const { wsId } = await params
 const { workflow: workflowFilter } = await searchParams

 let liveSkills: SkillSummary[] = []
 let workflows: WorkflowSummary[] = []
 let apiError: { title: string; detail: string } | null = null
 try {
 const [s, w] = await Promise.all([listSkills(), listWorkflows()])
 liveSkills = s.items
 workflows = w.items
 } catch (err) {
 apiError = kernelError(err)
 }

 const workflowTitleById = new Map<string, string>()
 for (const w of workflows) {
 workflowTitleById.set(w.id, workflowDisplayTitle(w.id, w.description, 50))
 }

 const allRows: SkillRow[] = liveSkills.map((s) => ({
 id: s.id,
 kind: s.kind,
 capability_tags: s.capability_tags,
 workflow_id: s.workflow_id,
 workflow_title: s.workflow_id
 ? workflowTitleById.get(s.workflow_id) ?? s.workflow_id
 : '—',
 head_version_seq: s.head_version_seq,
 head_created_at: s.head_created_at,
 }))

 // Workflow chips show every workflow that owns a skill, plus a
 // "(unscoped)" chip when there are skills with no workflow_id.
 // Built from the full row set so the chip strip doesn't disappear
 // after a filter narrows the list to one workflow.
 const workflowFacets = new Map<string, number>()
 let unscopedCount = 0
 for (const r of allRows) {
 if (!r.workflow_id) {
 unscopedCount += 1
 continue
 }
 workflowFacets.set(r.workflow_id, (workflowFacets.get(r.workflow_id) ?? 0) + 1)
 }
 const orderedFacets = [...workflowFacets.entries()].sort((a, b) => b[1] - a[1])

 const activeFilter = workflowFilter && workflowFilter.length > 0 ? workflowFilter : null
 const rows =
 activeFilter === null
 ? allRows
 : activeFilter === '_unscoped'
 ? allRows.filter((r) => !r.workflow_id)
 : allRows.filter((r) => r.workflow_id === activeFilter)

 const totalVersions = rows.reduce(
 (acc, r) => acc + (r.head_version_seq ?? 0),
 0,
 )
 const workflowCount = new Set(
 rows.map((r) => r.workflow_id).filter(Boolean),
 ).size

 return (
 <>
 <header className="page-header" style={{ marginBottom: 8 }}>
 <div>
 <h1 className="page-title">Skills library</h1>
 <p className="page-subtitle">
 {rows.length} skill{rows.length === 1 ? '' : 's'} ·{' '}
 {totalVersions} total versions · across {workflowCount} workflow
 {workflowCount === 1 ? '' : 's'} · workspace-scoped
 </p>
 </div>
 </header>

 {apiError && (
 <div role="alert" className="api-banner" style={{ marginBottom: 16 }}>
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )}

 {(orderedFacets.length > 0 || unscopedCount > 0) && (
 <div className="chip-strip" role="navigation" aria-label="Filter by workflow">
 <Link
 href={`/workspaces/${wsId}/skills`}
 className={`chip ${activeFilter === null ? 'active' : ''}`}
 >
 All <span className="chip-count">{allRows.length}</span>
 </Link>
 {orderedFacets.map(([wfId, count]) => (
 <Link
 key={wfId}
 href={`/workspaces/${wsId}/skills?workflow=${encodeURIComponent(wfId)}`}
 className={`chip ${activeFilter === wfId ? 'active' : ''}`}
 title={wfId}
 >
 {workflowTitleById.get(wfId) ?? wfId}
 <span className="chip-count">{count}</span>
 </Link>
 ))}
 {unscopedCount > 0 && (
 <Link
 href={`/workspaces/${wsId}/skills?workflow=_unscoped`}
 className={`chip ${activeFilter === '_unscoped' ? 'active' : ''}`}
 >
 (unscoped) <span className="chip-count">{unscopedCount}</span>
 </Link>
 )}
 </div>
 )}

 {rows.length === 0 ? (
 <div
 style={{
 background: 'var(--bg)',
 border: '1px dashed var(--border)',
 borderRadius: 8,
 padding: 24,
 color: 'var(--text-muted)',
 fontSize: 13,
 textAlign: 'center',
 }}
 >
 {activeFilter !== null ? (
 <>
 No skills match this filter.{' '}
 <Link href={`/workspaces/${wsId}/skills`} style={{ color: 'var(--accent)' }}>
 Clear filter
 </Link>
 .
 </>
 ) : (
 <>
 No skills registered yet. Run{' '}
 <code style={{ fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace" }}>
 make seed-demo
 </code>{' '}
 to populate sample workflows, or describe a new one under{' '}
 <Link
 href={`/workspaces/${wsId}/workflows/new`}
 style={{ color: 'var(--accent)' }}
 >
 New workflow
 </Link>
 .
 </>
 )}
 </div>
 ) : (
 <div className="table-wrap">
 <div className="skill-row head">
 <div>Skill</div>
 <div>Capabilities</div>
 <div>Kind</div>
 <div>Version</div>
 <div>Workflow</div>
 <div>Updated</div>
 <div />
 </div>
 {rows.map((r) => (
 <Link
 key={`${r.workflow_id ?? 'none'}::${r.id}`}
 href={`/workspaces/${wsId}/skills/${encodeURIComponent(r.id)}`}
 className="skill-row"
 >
 <div className="skill-name-cell">
 <span className="skill-name">{r.id}</span>
 <span className="skill-source">{r.workflow_title}</span>
 </div>
 <div className="cap-tags">
 {r.capability_tags.length === 0 ? (
 <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
 untagged
 </span>
 ) : (
 r.capability_tags.slice(0, 4).map((t) => (
 <span className="cap-tag" key={t}>
 {t}
 </span>
 ))
 )}
 </div>
 <div>
 <span className={`kind-chip ${r.kind}`}>{r.kind}</span>
 </div>
 <div className="skill-version">
 {r.head_version_seq !== null ? `v${r.head_version_seq}` : '—'}
 </div>
 <div className="skill-update" style={{ fontSize: 12 }}>
 {r.workflow_id ? r.workflow_title : '—'}
 </div>
 <div className="skill-update">
 {r.head_created_at ? relativeTime(r.head_created_at) : '—'}
 </div>
 <span className="skill-chev">›</span>
 </Link>
 ))}
 </div>
 )}
 </>
 )
}
