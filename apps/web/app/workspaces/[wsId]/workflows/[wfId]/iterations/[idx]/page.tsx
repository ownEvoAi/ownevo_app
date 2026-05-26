import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
 getIterationDetail,
 getProposal,
 kernelError,
 KernelApiError,
 type IterationCaseRow,
 type IterationDetail,
 type ProposalDetail,
} from '@/lib/api'
import { formatDateTime, formatScore, relativeTime } from '@/lib/format'
import { SkillDiff } from '@/app/components/skill-diff'

interface PageProps {
 params: Promise<{ wsId: string; wfId: string; idx: string }>
}

// PLAN row 8.4.8 — per-iteration drill-down. The lift chart on
// Overview now plots iteration_index x val_score; the case-by-case
// signal that drives improvement lives one level deeper. Click an
// iteration row and land here to see: which cases passed, which
// failed, what the agent predicted vs the ground truth, and which
// failure cluster anchored the next proposed instruction edit.
export default async function IterationDetailPage({ params }: PageProps) {
 const { wsId, wfId, idx } = await params
 const iterationIndex = Number.parseInt(idx, 10)
 if (!Number.isFinite(iterationIndex) || iterationIndex < 0) {
 notFound }

 let detail: IterationDetail
 try {
 detail = await getIterationDetail(wfId, iterationIndex)
 } catch (err) {
 if (err instanceof KernelApiError && err.status === 404) {
 notFound }
 const apiError = kernelError(err)
 return (
 <div role="alert" className="api-banner">
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )
 }

 // Fetch the proposal in parallel only when this iteration produced
 // one. Skill diff is the most-asked-for context on an iteration
 // "what did the agent want to change?" — so render it inline below
 // the case roster. Swallow errors here: the iteration page should
 // not 5xx because the proposal endpoint hiccupped.
 let proposal: ProposalDetail | null = null
 if (detail.proposal_id) {
 try {
 proposal = await getProposal(detail.proposal_id)
 } catch {
 proposal = null
 }
 }

 const failedCases = detail.cases.filter((c) => c.passed === false)
 const passedCases = detail.cases.filter((c) => c.passed === true)
 const unknownCases = detail.cases.filter((c) => c.passed === null)

 return (
 <>
 <nav className="crumb-row" style={{ marginTop: -8 }}>
 <Link href={`/workspaces/${wsId}/workflows/${wfId}`}>Overview</Link>
 <span className="sep">/</span>
 <span>Iteration {detail.iteration_index}</span>
 </nav>

 <header className="page-header" style={{ marginBottom: 12 }}>
 <div>
 <h1 className="page-title">Iteration {detail.iteration_index}</h1>
 <p className="page-subtitle">
 {detail.state} ·{' '}
 {detail.val_score !== null
 ? `val_score ${formatScore(detail.val_score)}`
 : 'val_score —'}
 {' · '}
 {detail.n_failed}/{detail.n_cases} failed
 {detail.ended_at !== null ? (
 <>
 {' · '}
 <span title={formatDateTime(detail.ended_at)}>
 {relativeTime(detail.ended_at)}
 </span>
 </>
 ) : null}
 </p>
 </div>
 <div className="page-actions" style={{ gap: 8 }}>
 {detail.proposal_id ? (
 <Link
 href={`/workspaces/${wsId}/proposals/${detail.proposal_id}`}
 className="btn btn-primary"
 style={{ fontSize: 12, padding: '6px 12px' }}
 >
 View proposal →
 </Link>
 ) : null}
 {detail.cluster_id ? (
 <Link
 href={`/workspaces/${wsId}/workflows/${wfId}/failures`}
 className="btn btn-secondary"
 style={{ fontSize: 12, padding: '6px 12px' }}
 >
 View failures →
 </Link>
 ) : null}
 </div>
 </header>

 <StateBanner detail={detail} />

 <section className="iteration-meta">
 <Meta
 label="Best ever before"
 value={
 detail.best_ever_score_before !== null
 ? formatScore(detail.best_ever_score_before)
 : '—'
 }
 />
 <Meta
 label="Best ever after"
 value={
 detail.best_ever_score_after !== null
 ? formatScore(detail.best_ever_score_after)
 : '—'
 }
 />
 <Meta
 label="Dominant cluster"
 value={detail.cluster_label ?? '— (no clusters)'}
 />
 <Meta
 label="Cases"
 value={`${detail.n_passed} passed · ${detail.n_failed} failed`}
 />
 <Meta
 label="Started"
 value={formatDateTime(detail.started_at)}
 />
 <Meta
 label="Ended"
 value={
 detail.ended_at !== null ? formatDateTime(detail.ended_at) : '—'
 }
 />
 </section>

 {proposal && (
 <section style={{ marginTop: 18 }}>
 <h2 className="section-title" style={{ marginBottom: 8 }}>
 {proposal.kind === 'skill'
 ? `Skill diff · ${proposal.skill_id ?? ''}`
 : `Artifact diff · ${proposal.kind ?? 'skill'}`}
 {proposal.parent_version_seq !== null
 ? ` v${proposal.parent_version_seq} → v${proposal.parent_version_seq + 1}`
 : ' · initial version'}
 {' · '}
 <Link
 href={`/workspaces/${wsId}/proposals/${proposal.id}`}
 style={{ fontSize: 12, color: 'var(--accent)' }}
 >
 Open proposal →
 </Link>
 </h2>
 <SkillDiff
 current={proposal.parent_version_content}
 proposed={proposal.proposed_content}
 parentVersionSeq={proposal.parent_version_seq}
 />
 </section>
 )}

 {failedCases.length > 0 && (
 <CaseSection
 label="Failed"
 tone="fail"
 wsId={wsId}
 cases={failedCases}
 />
 )}
 {unknownCases.length > 0 && (
 <CaseSection
 label="Unknown"
 tone="unknown"
 wsId={wsId}
 cases={unknownCases}
 />
 )}
 {passedCases.length > 0 && (
 <CaseSection
 label="Passed"
 tone="pass"
 wsId={wsId}
 cases={passedCases}
 />
 )}

 {detail.cases.length === 0 && (
 <div
 style={{
 background: 'var(--bg)',
 border: '1px dashed var(--border)',
 borderRadius: 8,
 padding: 28,
 textAlign: 'center',
 color: 'var(--text-muted)',
 fontSize: 13,
 }}
 >
 No per-case traces recorded for this iteration. (Older
 iterations from before the iteration_runner wrote traces will
 show empty here.)
 </div>
 )}
 </>
 )
}

// Plain-English explanation of what the iteration's terminal state
// means — non-developer domain experts shouldn't have to decode
// `gate-blocked-no-improvement` / `sandbox-error` enum text.
function StateBanner({ detail }: { detail: IterationDetail }) {
 const before = detail.best_ever_score_before
 const score = detail.val_score
 const after = detail.best_ever_score_after
 const fmt = (v: number | null) => (v !== null ? formatScore(v) : '—')

 if (detail.state === 'gate-pass') {
 if (score !== null && before !== null && score > before) {
 return (
 <div className="iter-state-banner pass">
 <strong>Gate passed.</strong> val_score improved from {fmt(before)} →{' '}
 {fmt(score)}. The proposed instruction edit was promoted; best-ever
 rose to {fmt(after)}.
 </div>
 )
 }
 return (
 <div className="iter-state-banner pass">
 <strong>Gate passed.</strong> Proposed change cleared the regression
 gate.
 </div>
 )
 }
 if (detail.state === 'gate-blocked-no-improvement') {
 return (
 <div className="iter-state-banner blocked">
 <strong>Gate blocked the change.</strong> val_score{' '}
 {fmt(score)} didn&rsquo;t beat the prior best ({fmt(before)}), so the
 proposal was rejected. Best-ever stays {fmt(after)}. The agent will
 try a different edit on the next iteration.
 </div>
 )
 }
 if (detail.state === 'gate-blocked-regression') {
 return (
 <div className="iter-state-banner blocked">
 <strong>Gate blocked the change.</strong> val_score regressed (
 {fmt(score)} vs {fmt(before)}). The proposed edit was rejected to
 protect the production skill.
 </div>
 )
 }
 if (detail.state === 'sandbox-error') {
 return (
 <div className="iter-state-banner error">
 <strong>Sandbox error.</strong> The proposed code raised an exception
 when the agent ran it. The iteration didn&rsquo;t score and no
 proposal was created.
 </div>
 )
 }
 if (detail.state === 'running') {
 return (
 <div className="iter-state-banner running">
 <strong>Running…</strong> The agent is still scoring eval cases. The
 roster below will fill in once the iteration finishes.
 </div>
 )
 }
 return null
}


function Meta({ label, value }: { label: string; value: string }) {
 return (
 <div className="iteration-meta-cell">
 <div className="iteration-meta-label">{label}</div>
 <div className="iteration-meta-value">{value}</div>
 </div>
 )
}

function CaseSection({
 label,
 tone,
 wsId,
 cases,
}: {
 label: string
 tone: 'fail' | 'pass' | 'unknown'
 wsId: string
 cases: IterationCaseRow[]
}) {
 return (
 <section style={{ marginTop: 18 }}>
 <h2 className="section-title" style={{ marginBottom: 8 }}>
 {label} · {cases.length}
 </h2>
 <div className={`iter-case-list iter-case-list-${tone}`}>
 <div className="iter-case-row iter-case-head">
 <span>Case</span>
 <span>Predicted</span>
 <span>Expected</span>
 <span>Fold</span>
 <span>Trace</span>
 </div>
 {cases.map((c) => (
 <Link
 key={c.trace_id}
 href={`/workspaces/${wsId}/traces/${c.trace_id}`}
 className="iter-case-row iter-case-row-rich"
 >
 <span className="iter-case-id-cell">
 <span className="iter-case-id" title={c.case_id}>
 {c.case_id}
 </span>
 {c.rationale ? (
 <span className="iter-case-rationale" title={c.rationale}>
 {c.rationale}
 </span>
 ) : null}
 </span>
 <span className={`iter-case-bool ${boolClass(c.predicted)}`}>
 {boolLabel(c.predicted)}
 </span>
 <span className={`iter-case-bool ${boolClass(c.expected)}`}>
 {boolLabel(c.expected)}
 </span>
 <span className="iter-case-fold">
 {c.is_test_fold ? 'test' : 'train'}
 </span>
 <span className="iter-case-trace">{c.trace_id.slice(0, 8)} ›</span>
 </Link>
 ))}
 </div>
 </section>
 )
}

function boolLabel(v: boolean | null): string {
 if (v === null) return '—'
 return v ? 'true' : 'false'
}

function boolClass(v: boolean | null): string {
 if (v === null) return ''
 return v ? 'is-true' : 'is-false'
}
