import Link from 'next/link'
import {
  kernelError,
  listSkills,
  listWorkflows,
  type SkillSummary,
  type WorkflowSummary,
} from '@/lib/api'
import { WORKFLOW_MOCKS } from '../workflows/[wfId]/mocks'
import { relativeTime } from '@/lib/format'

interface PageProps {
  params: Promise<{ wsId: string }>
}

interface SkillRow {
  id: string
  kind: SkillSummary['kind']
  capability_tags: string[]
  workflow_id: string | null
  workflow_title: string
  head_version_seq: number | null
  head_created_at: string | null
  isMock: boolean
}

// Skills library — PLAN row 8.0.4. Visual parity with
// www/preview/s26-rk7p3/11-skills-registry.html. Lists every skill
// across every workflow in the workspace, plus mock skills from
// the labour/contract/support positioning workflows so the page
// renders something even on an empty database.
export default async function SkillsLibraryPage({ params }: PageProps) {
  const { wsId } = await params

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
    workflowTitleById.set(w.id, w.description || w.id)
  }

  const liveRows: SkillRow[] = liveSkills.map((s) => ({
    id: s.id,
    kind: s.kind,
    capability_tags: s.capability_tags,
    workflow_id: s.workflow_id,
    workflow_title: s.workflow_id
      ? workflowTitleById.get(s.workflow_id) ?? s.workflow_id
      : '—',
    head_version_seq: s.head_version_seq,
    head_created_at: s.head_created_at,
    isMock: false,
  }))

  const mockRows: SkillRow[] = []
  for (const [wfKey, mock] of Object.entries(WORKFLOW_MOCKS)) {
    for (const s of mock.anatomy.skills) {
      mockRows.push({
        id: s.id,
        kind: s.kind,
        capability_tags: s.capability_tags,
        workflow_id: wfKey,
        workflow_title: mock.title,
        head_version_seq: s.head_version_seq,
        head_created_at: s.head_created_at,
        isMock: true,
      })
    }
  }

  // Live skills first, then mocks. Within each group keep the
  // backend's sort order (kind ASC, id ASC).
  const rows = [...liveRows, ...mockRows]

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
            {totalVersions} total versions · across {workflowCount} workflows ·
            workspace-scoped
          </p>
        </div>
      </header>

      {apiError && (
        <div role="alert" className="api-banner" style={{ marginBottom: 16 }}>
          <strong>{apiError.title}</strong> {apiError.detail}
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
          No skills registered yet. Seed the demand-prediction baseline with{' '}
          <code style={{ fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace" }}>
            scripts/seed_m5_baseline.py
          </code>{' '}
          or describe a workflow under{' '}
          <Link
            href={`/workspaces/${wsId}/workflows/new`}
            style={{ color: 'var(--accent)' }}
          >
            New workflow
          </Link>
          .
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
              key={`${r.isMock ? 'mock' : 'live'}::${r.workflow_id ?? 'none'}::${r.id}`}
              href={`/workspaces/${wsId}/skills/${encodeURIComponent(r.id)}`}
              className="skill-row"
            >
              <div className="skill-name-cell">
                <span className="skill-name">{r.id}</span>
                <span className="skill-source">
                  {r.workflow_title}
                  {r.isMock ? ' · MOCK' : ''}
                </span>
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
