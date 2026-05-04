import Link from 'next/link'
import { notFound } from 'next/navigation'
import { getProposal, KernelApiError, type ProposalDetail } from '@/lib/api'
import { formatDateTime, formatScore, relativeTime } from '@/lib/format'
import { DecideForm } from './decide-form'
import { SkillDiff } from './skill-diff'

interface PageProps {
  params: Promise<{ id: string }>
}

export default async function ProposalDetailPage({ params }: PageProps) {
  const { id } = await params

  let proposal: ProposalDetail
  try {
    proposal = await getProposal(id)
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
    throw err
  }

  const canDecide = proposal.state === 'gate-passed'

  return (
    <div>
      <nav
        className="crumb-row"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12,
          color: 'var(--text-muted)',
          marginBottom: 14,
        }}
      >
        <Link href="/inbox" style={{ color: 'var(--text-3)' }}>
          Inbox
        </Link>
        <span style={{ color: 'var(--text-faint)' }}>/</span>
        <span>{proposal.workflow.description}</span>
        <span style={{ color: 'var(--text-faint)' }}>/</span>
        <span>Proposal {proposal.id.slice(0, 8)}</span>
      </nav>

      <ProposalHeader proposal={proposal} />

      <div
        className="prop-grid"
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 320px',
          gap: 20,
          alignItems: 'start',
        }}
      >
        <div>
          <h2 className="section-title">
            Skill diff · {proposal.skill_id}
            {proposal.parent_version_seq !== null
              ? ` v${proposal.parent_version_seq} → v${proposal.parent_version_seq + 1}`
              : ' · initial version'}
          </h2>
          <SkillDiff
            current={proposal.parent_version_content}
            proposed={proposal.proposed_content}
          />

          <h2 className="section-title">Why this change</h2>
          <Rationale proposal={proposal} />

          {proposal.audit_entries.length > 0 && (
            <>
              <h2 className="section-title">Audit chain</h2>
              <AuditList entries={proposal.audit_entries} />
            </>
          )}
        </div>

        <aside>
          <GateResult proposal={proposal} />
          {proposal.expected_impact &&
            Object.keys(proposal.expected_impact).length > 0 && (
              <ExpectedImpact impact={proposal.expected_impact} />
            )}
          {canDecide ? (
            <DecideForm proposalId={proposal.id} />
          ) : (
            <DecisionRecorded proposal={proposal} />
          )}
        </aside>
      </div>
    </div>
  )
}

function ProposalHeader({ proposal }: { proposal: ProposalDetail }) {
  return (
    <div
      className="prop-header"
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        padding: '22px 26px',
        marginBottom: 20,
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <div className="prop-pills" style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        <span className={`pill ${pillVariant(proposal.state)}`}>{proposal.state}</span>
        {proposal.iteration.sandbox_error_class && (
          <span className="pill amber">
            sandbox: {proposal.iteration.sandbox_error_class}
          </span>
        )}
        <span className="pill outline">iter #{proposal.iteration.iteration_index}</span>
      </div>
      <h1
        className="prop-title"
        style={{
          fontSize: 22,
          fontWeight: 600,
          letterSpacing: '-0.02em',
          color: 'var(--text)',
          lineHeight: 1.3,
          marginBottom: 12,
        }}
      >
        {proposal.plain_language_summary}
      </h1>
      <div
        className="prop-meta-row"
        style={{
          display: 'flex',
          gap: 24,
          flexWrap: 'wrap',
          paddingTop: 16,
          borderTop: '1px solid var(--border)',
        }}
      >
        <Meta label="Workflow" value={proposal.workflow.description} />
        <Meta label="Skill" value={proposal.skill_id} />
        <Meta label="Created" value={`${relativeTime(proposal.created_at)} · ${formatDateTime(proposal.created_at)}`} />
        <Meta label="Workflow mode" value={proposal.workflow.mode} />
      </div>
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="prop-meta" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span
        className="prop-meta-label"
        style={{
          fontSize: 10.5,
          fontWeight: 500,
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
        }}
      >
        {label}
      </span>
      <span
        className="prop-meta-value"
        style={{ fontSize: 13, color: 'var(--text-2)', fontWeight: 500 }}
      >
        {value}
      </span>
    </div>
  )
}

function pillVariant(state: string): string {
  if (state === 'gate-passed') return 'accent'
  if (state === 'approved-awaiting-deploy' || state === 'deployed') return 'green'
  if (state === 'rejected') return 'red'
  if (state === 'gate-failed') return 'amber'
  return 'outline'
}

function GateResult({ proposal }: { proposal: ProposalDetail }) {
  return (
    <div
      className="sidebar-card"
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 16,
        boxShadow: 'var(--shadow-sm)',
        marginBottom: 12,
      }}
    >
      <div
        className="sidebar-title"
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          marginBottom: 10,
        }}
      >
        Regression gate
      </div>
      <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text)' }}>
        val_score: {formatScore(proposal.eval_score)}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 4 }}>
        best_ever: {formatScore(proposal.iteration.best_ever_score_after)} (was{' '}
        {formatScore(proposal.iteration.best_ever_score_before)})
      </div>
      {proposal.eval_rationale && (
        <p style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 10, lineHeight: 1.55 }}>
          {proposal.eval_rationale}
        </p>
      )}
    </div>
  )
}

function ExpectedImpact({ impact }: { impact: Record<string, unknown> }) {
  return (
    <div
      className="sidebar-card"
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 16,
        boxShadow: 'var(--shadow-sm)',
        marginBottom: 12,
      }}
    >
      <div
        className="sidebar-title"
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          marginBottom: 10,
        }}
      >
        Expected impact
      </div>
      <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {Object.entries(impact).map(([k, v]) => (
          <div key={k}>
            <dt
              style={{
                fontSize: 11,
                color: 'var(--text-muted)',
                textTransform: 'lowercase',
              }}
            >
              {k}
            </dt>
            <dd
              style={{
                fontSize: 14,
                fontWeight: 500,
                color: 'var(--text)',
                marginTop: 2,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {String(v)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function DecisionRecorded({ proposal }: { proposal: ProposalDetail }) {
  if (!proposal.approval) {
    return (
      <div
        className="sidebar-card"
        style={{
          background: 'var(--bg)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          padding: 16,
          boxShadow: 'var(--shadow-sm)',
        }}
      >
        <div className="sidebar-title">Decision</div>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 8 }}>
          Proposal is in <code>{proposal.state}</code> — not awaiting review.
        </p>
      </div>
    )
  }
  return (
    <div
      className="sidebar-card"
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 16,
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <div
        className="sidebar-title"
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          marginBottom: 10,
        }}
      >
        {proposal.approval.decision === 'approve' ? 'Approved' : 'Rejected'}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500 }}>
        {proposal.approval.decided_by}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}>
        {proposal.approval.approver_type} · {formatDateTime(proposal.approval.decided_at)}
      </div>
      {proposal.approval.comment && (
        <p
          style={{
            fontSize: 13,
            color: 'var(--text-2)',
            marginTop: 10,
            lineHeight: 1.5,
            background: 'var(--surface)',
            padding: '8px 10px',
            borderRadius: 5,
          }}
        >
          {proposal.approval.comment}
        </p>
      )}
      {proposal.approval.became_eval_case_id && (
        <p
          style={{
            fontSize: 11.5,
            color: 'var(--text-muted)',
            marginTop: 8,
          }}
        >
          Comment recorded as eval case{' '}
          <code>{proposal.approval.became_eval_case_id.slice(0, 8)}</code>
          (provenance: rejected-feedback).
        </p>
      )}
    </div>
  )
}

function Rationale({ proposal }: { proposal: ProposalDetail }) {
  return (
    <div
      className="rationale"
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: '16px 18px',
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.6 }}>
        {proposal.plain_language_summary}
      </p>
      {proposal.eval_rationale && (
        <p
          style={{
            fontSize: 12.5,
            color: 'var(--text-muted)',
            marginTop: 10,
            lineHeight: 1.5,
            fontFamily: 'ui-monospace, monospace',
          }}
        >
          gate: {proposal.eval_rationale}
        </p>
      )}
    </div>
  )
}

function AuditList({ entries }: { entries: ProposalDetail['audit_entries'] }) {
  return (
    <ol
      style={{
        listStyle: 'none',
        padding: 0,
        margin: 0,
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      {entries.map((e, i) => (
        <li
          key={e.id}
          style={{
            padding: '12px 16px',
            borderBottom:
              i < entries.length - 1 ? '1px solid var(--border)' : 'none',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            gap: 12,
          }}
        >
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>
              {e.kind}
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}>
              {e.actor}
            </div>
          </div>
          <div
            style={{
              fontSize: 11,
              color: 'var(--text-muted)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {formatDateTime(e.created_at)} · seq {e.seq}
          </div>
        </li>
      ))}
    </ol>
  )
}
