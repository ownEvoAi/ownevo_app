import Link from 'next/link'
import {
 kernelError,
 KernelApiError,
 listProposals,
 type ProposalState,
 type ProposalSummary,
} from '@/lib/api'
import { formatScore, relativeTime } from '@/lib/format'

interface PageProps {
 params: Promise<{ wsId: string; wfId: string }>
}

// Pending states drive the headline count + the "Review" CTA on rows.
const PENDING_STATES: ProposalState[] = ['gate-passed', 'pending']

// Visual variants reused from /inbox/page.tsx.
const STATE_VARIANT: Record<ProposalState, string> = {
 pending: 'outline',
 'in-gate': 'outline',
 'gate-failed': 'amber',
 'gate-passed': 'accent',
 'approved-awaiting-deploy': 'green',
 deployed: 'green',
 rejected: 'red',
 'rolled-back': 'red',
 'changes-requested': 'amber',
}

export default async function WorkflowProposalsPage({ params }: PageProps) {
 const { wsId, wfId } = await params

 let items: ProposalSummary[] = []
 let apiError: { title: string; detail: string } | null = null
 try {
 const list = await listProposals({ workflow_id: wfId, limit: 200 })
 items = list.items
 } catch (err) {
 if (!(err instanceof KernelApiError && err.status === 404)) {
 apiError = kernelError(err)
 }
 }

 const pending = items.filter((p) => PENDING_STATES.includes(p.state))
 const decided = items.filter((p) => !PENDING_STATES.includes(p.state))

 return (
 <>
 <header className="page-header" style={{ marginBottom: 8 }}>
 <div>
 <h1 className="page-title">Proposals</h1>
 <p className="page-subtitle">
 {items.length} total · {pending.length} pending review · {decided.length}{' '}
 decided
 </p>
 </div>
 </header>

 {apiError && (
 <div role="alert" className="api-banner" style={{ marginTop: 16 }}>
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )}

 {!apiError && items.length === 0 ? (
 <div
 style={{
 background: 'var(--bg)',
 border: '1px dashed var(--border)',
 borderRadius: 8,
 padding: 28,
 color: 'var(--text-muted)',
 fontSize: 13,
 lineHeight: 1.55,
 marginTop: 16,
 }}
 >
 <p style={{ margin: 0, marginBottom: 6 }}>
 <strong>No proposals yet.</strong> Run an iteration from the
 Overview tab. Each iteration that produces an instruction edit
 lands here in <code>gate-passed</code> state, ready for review.
 </p>
 </div>
 ) : null}

 {pending.length > 0 ? (
 <>
 <h2 className="group-head" style={{ marginTop: 20 }}>
 Pending review · {pending.length}
 </h2>
 <div className="proposal-table">
 {pending.map((p) => (
 <ProposalRow key={p.id} wsId={wsId} proposal={p} primary />
 ))}
 </div>
 </>
 ) : null}

 {decided.length > 0 ? (
 <>
 <h2 className="group-head" style={{ marginTop: 28 }}>
 Decided · {decided.length}
 </h2>
 <div className="proposal-table">
 {decided.map((p) => (
 <ProposalRow key={p.id} wsId={wsId} proposal={p} />
 ))}
 </div>
 </>
 ) : null}
 </>
 )
}

function ProposalRow({
 wsId,
 proposal,
 primary = false,
}: {
 wsId: string
 proposal: ProposalSummary
 primary?: boolean
}) {
 const variant = STATE_VARIANT[proposal.state] ?? 'outline'
 const isPending = PENDING_STATES.includes(proposal.state)
 return (
 <Link
 href={`/workspaces/${wsId}/proposals/${proposal.id}`}
 className={`proposal-row${primary ? ' primary' : ''}`}
 >
 <div className="proposal-row-main">
 <div className="proposal-row-meta">
 <span className={`pill ${variant}`}>{proposal.state}</span>
 <span className="proposal-row-dot">·</span>
 <span>Iter #{proposal.iteration_index}</span>
 <span className="proposal-row-dot">·</span>
 <span>{relativeTime(proposal.created_at)}</span>
 </div>
 <div className="proposal-row-title">{proposal.plain_language_summary}</div>
 <div className="proposal-row-foot">
 <span>Gate score: {formatScore(proposal.eval_score)}</span>
 <span className="proposal-row-dot">·</span>
 {proposal.kind === 'skill' && proposal.skill_id ? (
 <span>
 Skill: <code>{proposal.skill_id}</code>
 </span>
 ) : (
 <span>
 Artifact: <code>{proposal.kind ?? 'skill'}</code>
 </span>
 )}
 </div>
 </div>
 <div className="proposal-row-action">
 <span className={isPending ? 'btn btn-primary' : 'btn btn-secondary'}>
 {isPending ? 'Review →' : 'View'}
 </span>
 </div>
 </Link>
 )
}
