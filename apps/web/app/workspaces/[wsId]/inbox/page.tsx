import Link from 'next/link'
import { listProposals, type ProposalSummary } from '@/lib/api'
import { formatScore, relativeTime, workflowDisplayTitle } from '@/lib/format'

interface PageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ filter?: string }>
}

// Workspace-shell Inbox. Visual target:
// www/preview/s26-rk7p3/02-inbox.html — workspace switcher,
// Activity / Workflows / Library nav, Inbox marked active, filter
// chips driving the list view.
//
// Filters (mock parity, minus Escalations):
//   * all          — pending review + recently decided (default)
//   * proposals    — only proposals awaiting review (state=gate-passed)
//   * regression   — only gate-failed proposals (regression-blocked,
//                    no-improvement, or sandbox-error all collapse to
//                    `gate-failed` in the kernel)
//
// Mock also shows "Escalations" chip — those are human-decision-needed
// events from agent runs (e.g. support-09: "refund $5,200 exceeds
// agent autonomous limit"). No kernel concept for that today; it would
// be a separate `escalations` table fed by a `human_decision_required`
// AgentEvent. Filter intentionally omitted until that lands.

type Filter = 'all' | 'proposals' | 'regression'

function parseFilter(raw: string | undefined): Filter {
  if (raw === 'proposals' || raw === 'regression') return raw
  return 'all'
}

export default async function WorkspaceInboxPage({ params, searchParams }: PageProps) {
  // wsId is intentionally unread — D4 single-tenant means the kernel
  // ignores the slug. The param exists for URL stability and so the
  // workspace layout's nav can highlight the current workspace.
  await params
  const sp = await searchParams
  const filter = parseFilter(sp.filter)
  const root = '/workspaces/acme/inbox'

  let pendingData, regressionData, recentData
  try {
    ;[pendingData, regressionData, recentData] = await Promise.all([
      listProposals({ state: 'gate-passed', limit: 200 }),
      listProposals({ state: 'gate-failed', limit: 200 }),
      listProposals({ limit: 50 }),
    ])
  } catch (err) {
    return (
      <>
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
      </>
    )
  }

  const pending = pendingData.items
  const regression = regressionData.items
  const decided = recentData.items.filter(
    (p) => p.state !== 'gate-passed' && p.state !== 'in-gate' && p.state !== 'gate-failed',
  )
  const totalCount = recentData.total

  const chips: Array<{ key: Filter; label: string; count: number }> = [
    { key: 'all', label: 'All', count: totalCount },
    { key: 'proposals', label: 'Proposals', count: pendingData.total },
    { key: 'regression', label: 'Regression alerts', count: regressionData.total },
  ]

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Inbox</h1>
          <p className="page-subtitle">
            {pendingData.total} pending · {totalCount} total · refreshed just now
          </p>
        </div>
      </header>

      <div className="filters">
        {chips.map((c) => (
          <Link
            key={c.key}
            href={c.key === 'all' ? root : `${root}?filter=${c.key}`}
            className={`filter-chip${filter === c.key ? ' active' : ''}`}
          >
            {c.label}
            <span className="count">{c.count}</span>
          </Link>
        ))}
      </div>

      {filter === 'all' && (
        <>
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
        </>
      )}

      {filter === 'proposals' && (
        <>
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
        </>
      )}

      {filter === 'regression' && (
        <>
          <h2 className="section-title">Regression-blocked</h2>
          {regression.length === 0 ? (
            <EmptyState message="No proposals blocked by the regression gate." />
          ) : (
            <div className="inbox">
              {regression.map((p) => (
                <ProposalRow key={p.id} proposal={p} />
              ))}
            </div>
          )}
        </>
      )}
    </>
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
          <span
            className="inbox-source"
            title={proposal.workflow_description}
          >
            {workflowDisplayTitle(proposal.workflow_id, proposal.workflow_description, 60)}
          </span>
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
