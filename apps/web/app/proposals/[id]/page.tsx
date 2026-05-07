import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  getProposal,
  KernelApiError,
  type GateResultCases,
  type ProposalDetail,
} from '@/lib/api'
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
      <nav className="crumb-row">
        <Link href="/inbox">Inbox</Link>
        <span className="sep">/</span>
        <span>{proposal.workflow.description}</span>
        <span className="sep">/</span>
        <span>Proposal {proposal.id.slice(0, 8)}</span>
      </nav>

      <ProposalHeader proposal={proposal} />

      <div className="prop-grid">
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
            parentVersionSeq={proposal.parent_version_seq}
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
    <div className="prop-header">
      <div className="prop-pills">
        <span className={`pill ${pillVariant(proposal.state)}`}>
          {proposal.state}
        </span>
        {proposal.iteration.sandbox_error_class && (
          <span className="pill amber">
            sandbox: {proposal.iteration.sandbox_error_class}
          </span>
        )}
        <span className="pill outline">
          iter #{proposal.iteration.iteration_index}
        </span>
      </div>
      <h1 className="prop-title">{proposal.plain_language_summary}</h1>
      <div className="prop-meta-row">
        <Meta label="Workflow" value={proposal.workflow.description} />
        <Meta label="Skill" value={proposal.skill_id} />
        <Meta
          label="Created"
          value={`${relativeTime(proposal.created_at)} · ${formatDateTime(proposal.created_at)}`}
        />
        <Meta label="Workflow mode" value={proposal.workflow.mode} />
      </div>
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="prop-meta">
      <span className="prop-meta-label">{label}</span>
      <span className="prop-meta-value">{value}</span>
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
  const cases = proposal.gate_result_cases
  const verdict = gateVerdict(proposal, cases)

  return (
    <div className="sidebar-card">
      <div className="sidebar-title">Regression gate</div>

      <div className="gate-headline">
        <div className={`gate-icon ${verdict.tone}`}>
          <svg viewBox="0 0 16 16" aria-hidden>
            <path d={verdict.iconPath} />
          </svg>
        </div>
        <div>
          <div className="gate-status-text">{verdict.headline}</div>
          <div className="gate-status-meta">
            val_score: {formatScore(proposal.eval_score)} · best_ever{' '}
            {formatScore(proposal.iteration.best_ever_score_after)}
          </div>
        </div>
      </div>

      {cases && !cases.unknown && <CaseBreakdown cases={cases} />}

      {proposal.eval_rationale && (
        <p
          style={{
            fontSize: 12.5,
            color: 'var(--text-3)',
            marginTop: 12,
            lineHeight: 1.55,
          }}
        >
          {proposal.eval_rationale}
        </p>
      )}
    </div>
  )
}

function gateVerdict(
  proposal: ProposalDetail,
  cases: GateResultCases | null,
): { headline: string; tone: '' | 'amber' | 'red'; iconPath: string } {
  const checkPath = 'M3 8 L7 12 L13 4'
  const exclamPath = 'M8 3 L8 9 M8 11.5 L8 12.5'
  if (proposal.iteration.sandbox_error_class) {
    return {
      headline: `Sandbox: ${proposal.iteration.sandbox_error_class}`,
      tone: 'amber',
      iconPath: exclamPath,
    }
  }
  if (proposal.state === 'gate-failed') {
    const regressed = cases?.regressed.length ?? 0
    return {
      headline: regressed > 0 ? `${regressed} regression(s)` : 'Gate failed',
      tone: 'red',
      iconPath: exclamPath,
    }
  }
  const passed = cases?.passed.length ?? 0
  const total = (cases?.passed.length ?? 0) + (cases?.regressed.length ?? 0)
  if (total > 0) {
    return {
      headline: `${passed} / ${total} prior cases pass`,
      tone: '',
      iconPath: checkPath,
    }
  }
  return { headline: 'Gate passed', tone: '', iconPath: checkPath }
}

function CaseBreakdown({ cases }: { cases: GateResultCases }) {
  const sections: { label: string; rows: string[]; cls: string }[] = []
  if (cases.regressed.length > 0) {
    sections.push({
      label: `Regressed (${cases.regressed.length})`,
      rows: cases.regressed,
      cls: 'fail',
    })
  }
  if (cases.passed.length > 0) {
    sections.push({
      label: `Passed (${cases.passed.length})`,
      rows: cases.passed,
      cls: '',
    })
  }
  if (cases.newly_admitted.length > 0) {
    sections.push({
      label: `Newly admitted (${cases.newly_admitted.length})`,
      rows: cases.newly_admitted,
      cls: 'new',
    })
  }

  if (sections.length === 0) {
    return (
      <p
        style={{
          fontSize: 12,
          color: 'var(--text-muted)',
          marginTop: 6,
        }}
      >
        Gate had no prior eval cases yet (bootstrap iteration).
      </p>
    )
  }

  const checkPath = 'M3 8 L7 12 L13 4'
  const xPath = 'M4 4 L12 12 M12 4 L4 12'

  return (
    <div className="gate-list">
      {sections.map((section) => (
        <div key={section.label}>
          <div className="gate-section-label">{section.label}</div>
          {section.rows.map((row) => (
            <div
              key={`${section.label}:${row}`}
              className={`gate-case ${section.cls}`}
            >
              <span className="check">
                <svg viewBox="0 0 16 16" aria-hidden>
                  <path d={section.cls === 'fail' ? xPath : checkPath} />
                </svg>
              </span>
              <span className="case-name" title={row}>
                {row}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function ExpectedImpact({ impact }: { impact: Record<string, unknown> }) {
  return (
    <div className="sidebar-card">
      <div className="sidebar-title">Expected impact</div>
      <div className="impact-grid">
        {Object.entries(impact).map(([k, v]) => (
          <div key={k} className="impact-cell">
            <div className="impact-label">{k}</div>
            <div className="impact-value">{String(v)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function DecisionRecorded({ proposal }: { proposal: ProposalDetail }) {
  if (!proposal.approval) {
    return (
      <div className="sidebar-card">
        <div className="sidebar-title">Decision</div>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 8 }}>
          Proposal is in <code>{proposal.state}</code> — not awaiting review.
        </p>
      </div>
    )
  }
  return (
    <div className="sidebar-card">
      <div className="sidebar-title">
        {proposal.approval.decision === 'approve' ? 'Approved' : 'Rejected'}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500 }}>
        {proposal.approval.decided_by}
      </div>
      <div
        style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}
      >
        {proposal.approval.approver_type} ·{' '}
        {formatDateTime(proposal.approval.decided_at)}
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
          style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 8 }}
        >
          Comment recorded as eval case{' '}
          <code>{proposal.approval.became_eval_case_id.slice(0, 8)}</code>{' '}
          (provenance: rejected-feedback).
        </p>
      )}
    </div>
  )
}

function Rationale({ proposal }: { proposal: ProposalDetail }) {
  return (
    <div className="rationale">
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
