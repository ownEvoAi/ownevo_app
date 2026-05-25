import Link from 'next/link'
import {
  kernelError,
  listAudit,
  listWorkflows,
  type AuditEntryRow,
  type AuditList,
  type WorkflowSummary,
} from '@/lib/api'
import { relativeTime, workflowDisplayTitle } from '@/lib/format'

interface PageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ workflow?: string; kind?: string }>
}

// Recent activity feed — workspace-wide, every workflow.
//
// Reads `/api/audit` and renders each entry as a human-readable row.
// The Inbox surface only shows pending proposals; this one covers
// every state change (iterations starting/completing, clusters
// forming, deployments, rollbacks). Filterable by workflow + kind.
export default async function ActivityFeedPage({ params, searchParams }: PageProps) {
  const { wsId } = await params
  const { workflow: workflowFilter, kind: kindFilter } = await searchParams

  let audit: AuditList = { items: [], total: 0, truncated: false }
  let workflows: WorkflowSummary[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    const [a, w] = await Promise.all([
      listAudit({
        limit: 150,
        workflowId: workflowFilter || undefined,
        kind: kindFilter || undefined,
      }),
      listWorkflows(),
    ])
    audit = a
    workflows = w.items
  } catch (err) {
    apiError = kernelError(err)
  }

  // Use the short display title (first sentence, word-boundary
  // truncated) — workflow descriptions are full multi-paragraph
  // NL-gen prompts and dump the whole thing into the activity row
  // text if we don't shorten them here.
  const workflowTitleById = new Map(
    workflows.map((w) => [w.id, workflowDisplayTitle(w.id, w.description, 48)] as const),
  )

  // Bucket by day for visual grouping.
  const buckets = bucketByDay(audit.items)

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Activity</h1>
          <p className="page-subtitle">
            Every state change across this workspace · {audit.total} total
            {audit.truncated
              ? ` (showing ${audit.items.length} most recent)`
              : ''}
          </p>
        </div>
        <div className="page-actions">
          <Link
            href={`/workspaces/${wsId}/audit`}
            className="btn btn-secondary"
            style={{ fontSize: 12, padding: '6px 12px' }}
          >
            Raw audit log →
          </Link>
        </div>
      </header>

      <FilterStrip
        wsId={wsId}
        workflows={workflows}
        activeWorkflow={workflowFilter ?? null}
        activeKind={kindFilter ?? null}
      />

      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      {audit.items.length === 0 && !apiError ? (
        <div className="activity-empty">
          {workflowFilter || kindFilter ? (
            <>
              No activity matches this filter.{' '}
              <Link
                href={`/workspaces/${wsId}/activity`}
                style={{ color: 'var(--accent)' }}
              >
                Clear filters
              </Link>
              .
            </>
          ) : (
            <>
              No activity yet. Approving a proposal, running an iteration,
              or registering a workflow all show up here.
            </>
          )}
        </div>
      ) : (
        <div className="activity-list">
          {buckets.map((bucket) => (
            <section key={bucket.dayLabel} className="activity-bucket">
              <h2 className="activity-day">{bucket.dayLabel}</h2>
              <ul className="activity-rows">
                {bucket.entries.map((entry) => (
                  <ActivityRow
                    key={entry.id}
                    entry={entry}
                    wsId={wsId}
                    workflowTitleById={workflowTitleById}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  )
}

interface DayBucket {
  dayLabel: string
  entries: AuditEntryRow[]
}

function bucketByDay(entries: AuditEntryRow[]): DayBucket[] {
  const now = new Date()
  const today = startOfDay(now)
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const buckets = new Map<string, AuditEntryRow[]>()
  const labelOrder: string[] = []
  for (const e of entries) {
    const d = startOfDay(new Date(e.created_at))
    let label: string
    if (d.getTime() === today.getTime()) {
      label = 'Today'
    } else if (d.getTime() === yesterday.getTime()) {
      label = 'Yesterday'
    } else {
      label = d.toLocaleDateString(undefined, {
        weekday: 'long',
        month: 'short',
        day: 'numeric',
      })
    }
    if (!buckets.has(label)) {
      buckets.set(label, [])
      labelOrder.push(label)
    }
    buckets.get(label)!.push(e)
  }
  return labelOrder.map((label) => ({
    dayLabel: label,
    entries: buckets.get(label)!,
  }))
}

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

function FilterStrip({
  wsId,
  workflows,
  activeWorkflow,
  activeKind,
}: {
  wsId: string
  workflows: WorkflowSummary[]
  activeWorkflow: string | null
  activeKind: string | null
}) {
  // Limit workflow chips to those with audit footprint (any iteration
  // count > 0) to keep the strip short on workspaces with many
  // pre-launch rows.
  const visible = workflows.filter(
    (w) => w.iteration_count > 0 || (w.running_iteration_count ?? 0) > 0,
  )
  if (visible.length === 0 && !activeWorkflow && !activeKind) return null

  const baseHref = `/workspaces/${wsId}/activity`
  const hrefWith = (params: Record<string, string | null>) => {
    const qs = new URLSearchParams()
    if (params.workflow) qs.set('workflow', params.workflow)
    if (params.kind) qs.set('kind', params.kind)
    const s = qs.toString()
    return s ? `${baseHref}?${s}` : baseHref
  }

  return (
    <div className="chip-strip" role="navigation" aria-label="Filter activity">
      <Link
        href={baseHref}
        className={`chip ${!activeWorkflow && !activeKind ? 'active' : ''}`}
      >
        All
      </Link>
      {visible.map((w) => (
        <Link
          key={w.id}
          href={hrefWith({ workflow: w.id, kind: activeKind })}
          className={`chip ${activeWorkflow === w.id ? 'active' : ''}`}
          title={w.description}
        >
          {workflowDisplayTitle(w.id, w.description, 32)}
        </Link>
      ))}
      {KIND_FILTERS.map((k) => (
        <Link
          key={k.kind}
          href={hrefWith({ workflow: activeWorkflow, kind: k.kind })}
          className={`chip ${activeKind === k.kind ? 'active' : ''}`}
        >
          {k.label}
        </Link>
      ))}
    </div>
  )
}

const KIND_FILTERS: { kind: string; label: string }[] = [
  { kind: 'proposal-approved', label: 'Approvals' },
  { kind: 'gate-run-completed', label: 'Gate runs' },
  { kind: 'cluster-created', label: 'Clusters' },
  { kind: 'proposal-deployed', label: 'Deploys' },
]

function ActivityRow({
  entry,
  wsId,
  workflowTitleById,
}: {
  entry: AuditEntryRow
  wsId: string
  workflowTitleById: Map<string, string>
}) {
  const rendered = renderEntry(entry, wsId, workflowTitleById)
  return (
    <li className={`activity-row activity-${rendered.tone}`}>
      <span className={`activity-icon activity-${rendered.tone}`} aria-hidden>
        {rendered.glyph}
      </span>
      <div className="activity-body">
        <div className="activity-text">{rendered.text}</div>
        <div className="activity-meta">
          <span>{entry.actor}</span>
          <span>·</span>
          <span title={entry.created_at}>{relativeTime(entry.created_at)}</span>
          {rendered.href ? (
            <>
              <span>·</span>
              <Link href={rendered.href} className="activity-link">
                {rendered.linkLabel ?? 'Open →'}
              </Link>
            </>
          ) : null}
        </div>
      </div>
    </li>
  )
}

interface Rendered {
  glyph: string
  tone: 'green' | 'red' | 'amber' | 'accent' | 'neutral'
  text: React.ReactNode
  href: string | null
  linkLabel?: string
}

// Map audit kind + payload to a one-sentence human summary plus an
// optional click-through. Kept in one switch so adding a new kind is
// a single-place change. The `?? ''` casts keep TS happy when payload
// fields are missing (audit rows are JSONB free-form).
function renderEntry(
  entry: AuditEntryRow,
  wsId: string,
  workflowTitleById: Map<string, string>,
): Rendered {
  const p = entry.payload || {}
  const workflowId =
    typeof p.workflow_id === 'string' ? (p.workflow_id as string) : null
  const wfLabel = workflowId
    ? workflowTitleById.get(workflowId) ?? workflowId
    : null
  const iterIdx = typeof p.iteration_index === 'number' ? p.iteration_index : null

  switch (entry.kind) {
    case 'proposal-approved':
      return {
        glyph: '✓',
        tone: 'green',
        text: (
          <>
            Proposal{' '}
            <code>{(entry.related_id ?? '').slice(0, 8)}</code> approved
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: entry.related_id
          ? `/workspaces/${wsId}/proposals/${entry.related_id}`
          : null,
        linkLabel: 'View proposal →',
      }
    case 'proposal-rejected':
      return {
        glyph: '✕',
        tone: 'red',
        text: (
          <>
            Proposal{' '}
            <code>{(entry.related_id ?? '').slice(0, 8)}</code> rejected
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: entry.related_id
          ? `/workspaces/${wsId}/proposals/${entry.related_id}`
          : null,
      }
    case 'proposal-deployed':
      return {
        glyph: '↑',
        tone: 'green',
        text: (
          <>
            Proposal{' '}
            <code>{(entry.related_id ?? '').slice(0, 8)}</code> deployed
            {wfLabel ? <> to {wfLabel}</> : null}
          </>
        ),
        href: entry.related_id
          ? `/workspaces/${wsId}/proposals/${entry.related_id}`
          : null,
      }
    case 'proposal-rolled-back':
      return {
        glyph: '↺',
        tone: 'amber',
        text: (
          <>
            Proposal{' '}
            <code>{(entry.related_id ?? '').slice(0, 8)}</code> rolled back
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: entry.related_id
          ? `/workspaces/${wsId}/proposals/${entry.related_id}`
          : null,
      }
    case 'proposal-created':
      return {
        glyph: '+',
        tone: 'accent',
        text: (
          <>
            New proposal{' '}
            <code>{(entry.related_id ?? '').slice(0, 8)}</code> created
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: entry.related_id
          ? `/workspaces/${wsId}/proposals/${entry.related_id}`
          : null,
        linkLabel: 'Review →',
      }
    case 'gate-run-started':
      return {
        glyph: '▶',
        tone: 'neutral',
        text: (
          <>
            Iteration {iterIdx ?? '—'} started
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href:
          workflowId && iterIdx !== null
            ? `/workspaces/${wsId}/workflows/${workflowId}/iterations/${iterIdx}`
            : null,
      }
    case 'gate-run-completed': {
      const val =
        typeof p.val_score === 'number' ? (p.val_score as number) : null
      const state = typeof p.state === 'string' ? (p.state as string) : null
      return {
        glyph: state === 'gate-pass' ? '✓' : '◔',
        tone: state === 'gate-pass' ? 'green' : 'amber',
        text: (
          <>
            Iteration {iterIdx ?? '—'} finished{' '}
            {state ? `(${state})` : ''}
            {val !== null ? <> · val_score {val.toFixed(3)}</> : null}
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href:
          workflowId && iterIdx !== null
            ? `/workspaces/${wsId}/workflows/${workflowId}/iterations/${iterIdx}`
            : null,
      }
    }
    case 'cluster-created': {
      const label = typeof p.label === 'string' ? (p.label as string) : ''
      const severity =
        typeof p.severity === 'string' ? (p.severity as string) : ''
      return {
        glyph: '◆',
        tone: severity === 'high' ? 'red' : 'amber',
        text: (
          <>
            Cluster <code>{label || (entry.related_id ?? '').slice(0, 8)}</code>{' '}
            ({severity || 'unrated'})
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: workflowId
          ? `/workspaces/${wsId}/workflows/${workflowId}/failures`
          : null,
      }
    }
    case 'cluster-relabeled':
      return {
        glyph: '✎',
        tone: 'neutral',
        text: (
          <>
            Cluster relabeled
            {wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: workflowId
          ? `/workspaces/${wsId}/workflows/${workflowId}/failures`
          : null,
      }
    case 'eval-case-added':
      return {
        glyph: '+',
        tone: 'neutral',
        text: (
          <>
            Eval case added{wfLabel ? <> to {wfLabel}</> : null}
          </>
        ),
        href: workflowId
          ? `/workspaces/${wsId}/workflows/${workflowId}/eval-cases`
          : null,
      }
    case 'skill-version-created': {
      const skillId = typeof p.skill_id === 'string' ? (p.skill_id as string) : null
      return {
        glyph: '◷',
        tone: 'neutral',
        text: (
          <>
            New skill version
            {skillId ? (
              <>
                {' for '}
                <code>{skillId}</code>
              </>
            ) : null}
          </>
        ),
        href: skillId
          ? `/workspaces/${wsId}/skills/${encodeURIComponent(skillId)}`
          : null,
      }
    }
    case 'workflow-created':
      return {
        glyph: '✸',
        tone: 'accent',
        text: (
          <>
            Workflow{' '}
            <code>{typeof p.workflow_id === 'string' ? (p.workflow_id as string) : ''}</code>{' '}
            created
          </>
        ),
        href: workflowId ? `/workspaces/${wsId}/workflows/${workflowId}` : null,
      }
    case 'deployment-created':
      return {
        glyph: '↑',
        tone: 'green',
        text: (
          <>
            Deployment created{wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: workflowId ? `/workspaces/${wsId}/workflows/${workflowId}` : null,
      }
    case 'deployment-updated':
      return {
        glyph: '↻',
        tone: 'amber',
        text: (
          <>
            Deployment updated{wfLabel ? <> on {wfLabel}</> : null}
          </>
        ),
        href: workflowId ? `/workspaces/${wsId}/workflows/${workflowId}` : null,
      }
    case 'meta-eval-result':
      return {
        glyph: '★',
        tone: 'accent',
        text: <>Meta-eval result recorded</>,
        href: `/workspaces/${wsId}/audit`,
      }
    case 'fix-shipped-langsmith':
      return {
        glyph: '↗',
        tone: 'green',
        text: (
          <>
            Fix shipped to LangSmith
            {entry.related_id ? (
              <>
                {' '}(proposal <code>{entry.related_id.slice(0, 8)}</code>)
              </>
            ) : null}
          </>
        ),
        href: entry.related_id
          ? `/workspaces/${wsId}/proposals/${entry.related_id}`
          : `/workspaces/${wsId}/audit`,
      }
    case 'fix-exported-copilot-studio':
      return {
        glyph: '↗',
        tone: 'green',
        text: (
          <>
            Fix delivered to Copilot Studio
            {entry.related_id ? (
              <>
                {' '}(proposal <code>{entry.related_id.slice(0, 8)}</code>)
              </>
            ) : null}
          </>
        ),
        href: entry.related_id
          ? `/workspaces/${wsId}/proposals/${entry.related_id}`
          : `/workspaces/${wsId}/audit`,
      }
    case 'eval-cases-pushed-copilot-studio': {
      const caseCount = typeof p.case_count === 'number' ? p.case_count : null
      return {
        glyph: '↗',
        tone: 'accent',
        text: (
          <>
            {caseCount !== null ? `${caseCount} eval case${caseCount === 1 ? '' : 's'}` : 'Eval cases'}
            {' '}pushed to Copilot Studio
            {wfLabel ? <> (<code>{wfLabel}</code>)</> : null}
          </>
        ),
        href: workflowId
          ? `/workspaces/${wsId}/workflows/${workflowId}`
          : `/workspaces/${wsId}/audit`,
      }
    }
    case 'schema-migration':
      return {
        glyph: '⚠',
        tone: 'red',
        text: <>Schema migration applied</>,
        href: `/workspaces/${wsId}/audit`,
      }
    default:
      return {
        glyph: '·',
        tone: 'neutral',
        text: <>{entry.kind}</>,
        href: `/workspaces/${wsId}/audit`,
      }
  }
}
