import Link from 'next/link'
import { listProposals, type ProposalSummary } from '@/lib/api'
import { formatScore, relativeTime } from '@/lib/format'

// Server Component — runs on the Next.js server, fetches the kernel
// REST API directly. The browser never holds the kernel URL.
//
// W2.5 scope: surface every proposal that has reached `gate-passed`
// (the only state where Approve/Reject is legal per
// docs/STATE_MACHINES.md) plus everything that has already been
// decided, so the queue shows recent history. W5 polish adds filter
// chips + per-workflow tabs.

export default async function InboxPage() {
  let data
  try {
    data = await listProposals({ limit: 50 })
  } catch (err) {
    return (
      <div>
        <header className="page-header">
          <div>
            <h1 className="page-title">Inbox</h1>
            <p className="page-subtitle">
              Failed to reach the kernel API. Is{' '}
              <code>uvicorn ownevo_kernel.api.app:app</code> running on port 8000?
            </p>
          </div>
        </header>
        <pre style={{ color: 'var(--red, #dc2626)', whiteSpace: 'pre-wrap' }}>
          {err instanceof Error ? err.message : String(err)}
        </pre>
      </div>
    )
  }

  const pending = data.items.filter((p) => p.state === 'gate-passed')
  const decided = data.items.filter(
    (p) => p.state !== 'gate-passed' && p.state !== 'in-gate',
  )

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Inbox</h1>
          <p className="page-subtitle">
            {pending.length} pending · {data.total} total · refreshed just now
          </p>
        </div>
      </header>

      <div className="filters">
        <button className="filter-chip active" type="button">
          Pending<span className="count">{pending.length}</span>
        </button>
        <button className="filter-chip" type="button">
          All<span className="count">{data.total}</span>
        </button>
      </div>

      <h2 className="section-title">Awaiting review</h2>
      {pending.length === 0 ? (
        <EmptyState message="No proposals waiting on a decision." />
      ) : (
        <div className="inbox">
          {pending.map((p) => (
            <ProposalRow key={p.id} proposal={p} primary />
          ))}
        </div>
      )}

      {decided.length > 0 && (
        <>
          <h2 className="section-title" style={{ marginTop: 24 }}>
            Recently decided
          </h2>
          <div className="inbox">
            {decided.map((p) => (
              <ProposalRow key={p.id} proposal={p} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function ProposalRow({
  proposal,
  primary = false,
}: {
  proposal: ProposalSummary
  primary?: boolean
}) {
  return (
    <Link
      href={`/proposals/${proposal.id}`}
      className={primary ? 'inbox-item featured' : 'inbox-item'}
      style={{ textDecoration: 'none' }}
    >
      <div className="inbox-icon proposal">
        <svg viewBox="0 0 16 16" aria-hidden>
          <path d="M2 3 L14 3 L14 11 L9 11 L6 14 L6 11 L2 11 Z M5 7 L11 7 M5 9 L9 9" />
        </svg>
      </div>
      <div className="inbox-body">
        <div className="inbox-meta-row">
          <span className="inbox-source">{proposal.workflow_description}</span>
          <span className="inbox-dot">·</span>
          <StatePill state={proposal.state} />
          <span className="inbox-dot">·</span>
          <span className="inbox-age">{relativeTime(proposal.created_at)}</span>
        </div>
        <div className="inbox-title">{proposal.plain_language_summary}</div>
        <div className="inbox-foot">
          <span className="gate-badge">
            <svg
              style={{ width: 12, height: 12 }}
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              aria-hidden
            >
              <path d="M3 8 L7 12 L13 4" />
            </svg>
            Gate score: {formatScore(proposal.eval_score)}
          </span>
          <span>·</span>
          <span>
            Skill: <span style={{ color: 'var(--accent)' }}>{proposal.skill_id}</span>
          </span>
          <span>·</span>
          <span>Iter #{proposal.iteration_index}</span>
        </div>
      </div>
      <div className="inbox-action">
        <span
          className={primary ? 'btn btn-primary' : 'btn btn-secondary'}
          style={{ fontSize: 12, padding: '6px 12px' }}
        >
          {proposal.state === 'gate-passed' ? 'Review →' : 'View'}
        </span>
      </div>
    </Link>
  )
}

function StatePill({ state }: { state: string }) {
  const variantByState: Record<string, string> = {
    'gate-passed': 'accent',
    'approved-awaiting-deploy': 'green',
    deployed: 'green',
    rejected: 'red',
    'gate-failed': 'amber',
    'in-gate': 'outline',
  }
  const variant = variantByState[state] ?? 'outline'
  return <span className={`pill ${variant}`}>{state}</span>
}

function EmptyState({ message }: { message: string }) {
  return (
    <div
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        padding: '24px 28px',
        color: 'var(--text-muted)',
        fontSize: 14,
      }}
    >
      {message}
    </div>
  )
}
