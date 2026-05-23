import Link from 'next/link'
import {
  getWorkflowAnatomy,
  kernelError,
  listAudit,
  type AmbiguityFinding,
  type AmbiguityReport,
  type AuditEntryRow,
  type AuditList,
  type DesignAgentLog,
  type DesignAgentLogEntry,
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
  'design-agent-negotiation': 'outline',
  'design-agent-ambiguity': 'amber',
  'workflow-agent-model-changed': 'accent',
}

const DISCOVERY_KIND_LABEL: Record<DesignAgentLogEntry['kind'], string> = {
  metric: 'Metric trade-off',
  ambiguity: 'Ambiguity',
  trigger: 'Trigger',
  surface: 'Surface',
  premise: 'Premise',
}

const AMBIGUITY_SEVERITY_TONE: Record<AmbiguityFinding['severity'], string> = {
  high: 'red',
  medium: 'amber',
  low: 'outline',
}

// Workflow-scoped audit trail. Audit entries are workspace-level today
// (D4 single-tenant), so this view filters to entries whose related_id
// ties back to this workflow's iterations / proposals / clusters via
// the ?workflow_id= server-side filter on /api/audit.
export default async function WorkflowAuditPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let audit: AuditList = { items: [], total: 0, truncated: false }
  let designAgentLog: DesignAgentLog | null = null
  let apiError: { title: string; detail: string } | null = null

  // Run both fetches in parallel; let each fail independently so a transient
  // anatomy error doesn't blank the audit trail (and vice versa).
  const [auditResult, anatomyResult] = await Promise.allSettled([
    listAudit({ workflowId: wfId, limit: 200 }),
    getWorkflowAnatomy(wfId),
  ])
  if (auditResult.status === 'fulfilled') {
    audit = auditResult.value
  } else {
    apiError = kernelError(auditResult.reason)
  }
  if (anatomyResult.status === 'fulfilled') {
    designAgentLog = anatomyResult.value.design_agent_log
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
          <a
            href={`/workspaces/${wsId}/workflows/${wfId}/audit/export`}
            className="btn btn-secondary"
            download
          >
            Export chain
          </a>
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

      {designAgentLog && designAgentLog.discovery_transcript.length > 0 ? (
        <DiscoveryTranscriptCard log={designAgentLog} />
      ) : null}

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

// Friendly read of the design-agent discovery session that ran before
// `generateWorkflow` fired. The same content lives one-row-per-Q/A in
// `audit_entries` for hash-chain integrity (PLAN 9.1.4); this card is
// the grouped, human-readable view a non-engineer reviewer wants.
function DiscoveryTranscriptCard({ log }: { log: DesignAgentLog }) {
  const transcript = [...log.discovery_transcript].sort(
    (a, b) => a.question_index - b.question_index,
  )
  const answered = transcript.filter(
    (e) => e.answer !== null && e.answer.trim() !== '',
  ).length
  const report = log.ambiguity_report
  return (
    <section
      style={{
        marginTop: 16,
        marginBottom: 16,
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 16,
      }}
    >
      <h2 className="section-title" style={{ marginTop: 0, marginBottom: 4 }}>
        Discovery transcript
      </h2>
      <p
        style={{
          margin: 0,
          marginBottom: 12,
          fontSize: 12,
          color: 'var(--text-muted)',
        }}
      >
        Design agent · {answered} of {transcript.length} question
        {transcript.length === 1 ? '' : 's'} answered before generation
      </p>
      <ol
        style={{
          listStyle: 'none',
          padding: 0,
          margin: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        {transcript.map((entry) => (
          <TranscriptRow key={entry.question_index} entry={entry} />
        ))}
      </ol>
      {report && report.findings.length > 0 ? (
        <AmbiguityFindingsCard report={report} />
      ) : null}
    </section>
  )
}

function TranscriptRow({ entry }: { entry: DesignAgentLogEntry }) {
  const kindLabel = DISCOVERY_KIND_LABEL[entry.kind] ?? entry.kind
  const skipped = entry.answer === null || entry.answer.trim() === ''
  return (
    <li
      style={{
        borderLeft: '3px solid var(--border)',
        paddingLeft: 12,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12,
          color: 'var(--text-muted)',
          marginBottom: 4,
        }}
      >
        <span>Q{entry.question_index + 1}</span>
        <span className="pill outline" style={{ fontSize: 11 }}>
          {kindLabel}
        </span>
      </div>
      <div style={{ fontSize: 13, marginBottom: 4 }}>{entry.question}</div>
      <div
        style={{
          fontSize: 13,
          color: skipped ? 'var(--text-muted)' : 'var(--text)',
          fontStyle: skipped ? 'italic' : 'normal',
          whiteSpace: 'pre-wrap',
          overflowWrap: 'break-word',
        }}
      >
        → {skipped ? 'skipped' : entry.answer}
      </div>
    </li>
  )
}

function AmbiguityFindingsCard({ report }: { report: AmbiguityReport }) {
  return (
    <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px dashed var(--border)' }}>
      <h3
        className="section-title"
        style={{ marginTop: 0, marginBottom: 4, fontSize: 14 }}
      >
        Ambiguity findings
      </h3>
      <p
        style={{
          margin: 0,
          marginBottom: 10,
          fontSize: 12,
          color: 'var(--text-muted)',
        }}
      >
        {report.findings.length} finding
        {report.findings.length === 1 ? '' : 's'}
        {report.high_severity_count > 0
          ? ` · ${report.high_severity_count} high severity`
          : ''}
      </p>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {report.findings.map((finding) => (
          <AmbiguityFindingRow key={`${finding.kind}:${finding.location}`} finding={finding} />
        ))}
      </ul>
    </div>
  )
}

function AmbiguityFindingRow({ finding }: { finding: AmbiguityFinding }) {
  const tone = AMBIGUITY_SEVERITY_TONE[finding.severity] ?? 'outline'
  return (
    <li
      style={{
        background: 'var(--bg-elev)',
        border: '1px solid var(--border)',
        borderRadius: 6,
        padding: 10,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12,
          marginBottom: 4,
        }}
      >
        <span className={`pill ${tone}`} style={{ fontSize: 11 }}>
          {finding.severity}
        </span>
        <span style={{ color: 'var(--text-muted)' }}>{finding.kind}</span>
        <code style={{ color: 'var(--text-muted)', fontSize: 11 }}>
          {finding.location}
        </code>
      </div>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2 }}>
        {finding.summary}
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        Suggested follow-up: {finding.suggested_question}
      </div>
    </li>
  )
}
