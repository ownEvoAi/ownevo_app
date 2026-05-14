import Link from 'next/link'
import {
  kernelError,
  listAudit,
  type AuditEntryRow,
  type AuditList,
} from '@/lib/api'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

const KIND_TONE: Record<string, string> = {
  'proposal-approved': 'green',
  'proposal-deployed': 'green',
  'proposal-rejected': 'red',
  'proposal-rolled-back': 'red',
  'gate-run-completed': 'accent',
  'gate-run-started': 'outline',
  'cluster-created': 'amber',
  'cluster-relabeled': 'amber',
  'workflow-created': 'accent',
  'meta-eval-result': 'accent',
  'skill-version-created': 'outline',
  'eval-case-added': 'outline',
  'schema-migration': 'red',
  'deployment-created': 'green',
  'deployment-updated': 'amber',
}

// Workflow-scoped audit trail. Audit entries are workspace-level today
// (D4 single-tenant), so this view filters to entries whose related_id
// ties back to this workflow's iterations / proposals / clusters via
// the ?workflow_id= server-side filter on /api/audit.
export default async function WorkflowAuditPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let audit: AuditList = { items: [], total: 0, truncated: false }
  let apiError: { title: string; detail: string } | null = null

  try {
    audit = await listAudit({ workflowId: wfId, limit: 200 })
  } catch (err) {
    apiError = kernelError(err)
  }

  return (
    <>
      <header className="page-header" style={{ marginBottom: 8 }}>
        <div>
          <h1 className="page-title">Audit trail</h1>
          <p className="page-subtitle">
            Append-only log scoped to this workflow · {audit.total}{' '}
            {audit.total === 1 ? 'entry' : 'entries'}
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
            Workspace audit ›
          </Link>
        </div>
      </header>

      {apiError && (
        <div role="alert" className="api-banner" style={{ marginTop: 12 }}>
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      {audit.items.length === 0 && !apiError ? (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 28,
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: 13,
            lineHeight: 1.55,
            marginTop: 16,
          }}
        >
          <p style={{ margin: 0, marginBottom: 4 }}>
            <strong>No audit entries for this workflow yet.</strong>
          </p>
          <p style={{ margin: 0, fontSize: 12 }}>
            Approving a proposal or running an iteration writes one.
            Workspace-level events (e.g. <code>schema-migration</code>) live in
            the workspace audit instead.
          </p>
        </div>
      ) : (
        <ol className="audit-list" style={{ marginTop: 12 }}>
          {audit.items.map((entry) => (
            <AuditRow key={entry.id} entry={entry} />
          ))}
        </ol>
      )}
    </>
  )
}

function AuditRow({ entry }: { entry: AuditEntryRow }) {
  const tone = KIND_TONE[entry.kind] ?? 'outline'
  return (
    <li className="audit-row">
      <details>
        <summary>
          <span className="audit-seq">#{entry.seq}</span>
          <span className={`pill ${tone}`}>{entry.kind}</span>
          <span className="audit-actor">{entry.actor}</span>
          <span className="audit-when">{formatWhen(entry.created_at)}</span>
        </summary>
        <div className="audit-detail">
          {entry.related_id && (
            <div className="audit-related">
              related_id <code>{entry.related_id}</code>
            </div>
          )}
          <pre className="audit-payload">{JSON.stringify(entry.payload, null, 2)}</pre>
        </div>
      </details>
    </li>
  )
}

function formatWhen(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}
