import {
  kernelErrorMessage,
  listAudit,
  type AuditEntryRow,
  type AuditList,
} from '../../../../lib/api'
import { VerifyButton } from './verify-button'

interface PageProps {
  params: Promise<{ wsId: string }>
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

// W7 slice 4 — Audit trail.
//
// Workspace-scoped (D4 single-tenant — no workspace_id column yet, so
// this is the entire log). Visual target:
// www/preview/s26-rk7p3/08-audit.html.
//
// The "verify chain" button lives in a client island (verify-button.tsx)
// and POSTs /api/audit/verify via a Server Action. Result renders
// inline below the button.
export default async function WorkspaceAuditPage({ params }: PageProps) {
  const { wsId } = await params

  let audit: AuditList = { items: [], total: 0, truncated: false }
  let apiError: string | null = null

  try {
    audit = await listAudit({ limit: 200 })
  } catch (err) {
    apiError = kernelErrorMessage(err)
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Audit trail</h1>
          <p className="page-subtitle">
            Append-only log of every state change in this workspace · {audit.total} entries
            {audit.truncated ? ` (showing ${audit.items.length} most recent)` : ''}
          </p>
        </div>
        <div className="page-actions">
          <VerifyButton wsId={wsId} />
        </div>
      </header>

      {apiError && (
        <div role="alert" className="api-banner">
          <strong>Kernel API not reachable.</strong> {apiError}
        </div>
      )}

      {audit.items.length === 0 && !apiError ? (
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
          No audit entries yet. Approving a proposal or running an iteration writes one.
        </div>
      ) : (
        <ol className="audit-list">
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
