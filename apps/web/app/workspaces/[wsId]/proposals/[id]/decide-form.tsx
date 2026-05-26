'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { decideAction } from './actions'

// Client island for the Approve/Reject form. The actual mutation runs
// server-side via the `decideAction` Server Action — the client just
// collects the comment and dispatches; on success it triggers a
// router.refresh so the page re-fetches the proposal (which now
// shows the recorded decision instead of the form).
//
// `wsId` is threaded through so revalidatePath can target the
// workspace-scoped routes (proposal detail + audit + Health).

export function DecideForm({
 proposalId,
 wsId,
 demoMode = false,
}: {
 proposalId: string
 wsId: string
 demoMode?: boolean
}) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [comment, setComment] = useState('')
 const [decidedBy, setDecidedBy] = useState('human:reviewer')
 const [error, setError] = useState<string | null>(null)

 function handleDecision(decision: 'approve' | 'reject' | 'request-changes') {
 setError(null)
 if (decision === 'request-changes' && !comment.trim() ) {
 setError('Add a comment first — the steering text drives the next iteration.')
 return
 }
 startTransition(async () => {
 const result = await decideAction({
 proposalId,
 wsId,
 decision,
 decidedBy,
 comment: comment.trim() || undefined,
 })
 if (!result.ok) {
 setError(result.error)
 return
 }
 // Server Action returned success; re-fetch the page so the
 // sidebar swaps to "Recorded decision".
 router.refresh })
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
 className="reviewer-row"
 style={{
 display: 'flex',
 alignItems: 'center',
 gap: 10,
 marginBottom: 12,
 }}
 >
 <div
 className="reviewer-avatar"
 style={{
 width: 28,
 height: 28,
 borderRadius: '50%',
 background: 'var(--surface-2)',
 color: 'var(--text-2)',
 display: 'flex',
 alignItems: 'center',
 justifyContent: 'center',
 fontSize: 11,
 fontWeight: 600,
 }}
 >
 {decidedBy.replace('human:', '').slice(0, 2).toUpperCase() }
 </div>
 <div>
 <input
 type="text"
 value={decidedBy}
 onChange={(e) => setDecidedBy(e.target.value)}
 disabled={isPending}
 style={{
 fontSize: 13,
 fontWeight: 500,
 color: 'var(--text)',
 background: 'transparent',
 border: 0,
 outline: 'none',
 padding: 0,
 fontFamily: 'inherit',
 }}
 aria-label="Reviewer identity"
 />
 <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
 Reviewer · scaffold
 </div>
 </div>
 </div>

 <textarea
 value={comment}
 onChange={(e) => setComment(e.target.value)}
 disabled={isPending}
 placeholder="Optional comment. Required for Request changes — the steering text drives the next iteration. On Reject, the comment becomes a new eval case automatically."
 style={{
 width: '100%',
 minHeight: 90,
 padding: '10px 12px',
 border: '1px solid var(--border)',
 borderRadius: 6,
 fontFamily: 'inherit',
 fontSize: 13,
 color: 'var(--text)',
 background: 'var(--bg)',
 resize: 'vertical',
 }}
 />
 <div
 className="reviewer-hint"
 style={{
 fontSize: 11,
 color: 'var(--text-muted)',
 marginTop: 4,
 lineHeight: 1.4,
 }}
 >
 Approval transitions to <code>approved-awaiting-deploy</code>. Request
 changes keeps the proposal alive — your comment feeds the next iteration.
 Rejection is terminal; comment becomes a regression eval case.
 </div>

 <div
 className="reviewer-actions"
 style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 14 }}
 >
 <button
 type="button"
 onClick={() => handleDecision('approve')}
 disabled={isPending || demoMode}
 title={demoMode ? 'Disabled in read-only demo' : undefined}
 className="btn btn-primary"
 style={{
 width: '100%',
 justifyContent: 'center',
 padding: '8px 14px',
 fontSize: 13,
 }}
 >
 {isPending ? 'Submitting…' : 'Approve & advance'}
 </button>
 <button
 type="button"
 onClick={() => handleDecision('request-changes')}
 disabled={isPending || demoMode}
 title={demoMode ? 'Disabled in read-only demo' : undefined}
 className="btn btn-secondary"
 style={{
 width: '100%',
 justifyContent: 'center',
 padding: '8px 14px',
 fontSize: 13,
 }}
 >
 Request changes
 </button>
 <button
 type="button"
 onClick={() => handleDecision('reject')}
 disabled={isPending || demoMode}
 title={demoMode ? 'Disabled in read-only demo' : undefined}
 className="btn btn-ghost"
 style={{
 width: '100%',
 justifyContent: 'center',
 padding: '8px 14px',
 fontSize: 13,
 color: 'var(--red, #dc2626)',
 }}
 >
 Reject
 </button>
 </div>

 {demoMode && (
 <p
 style={{
 fontSize: 11.5,
 color: 'var(--text-muted)',
 marginTop: 10,
 lineHeight: 1.4,
 }}
 >
 Approve / reject are disabled in this read-only demo. Self-host
 from{' '}
 <a
 href="https://github.com/ownEvoAi/ownevo_app"
 target="_blank"
 rel="noreferrer"
 >
 GitHub
 </a>{' '}
 to run the full flow.
 </p>
 )}

 {error && (
 <p
 role="alert"
 style={{
 fontSize: 12,
 color: 'var(--red, #dc2626)',
 marginTop: 12,
 background: 'rgba(239, 68, 68, 0.08)',
 padding: '8px 10px',
 borderRadius: 5,
 }}
 >
 {error}
 </p>
 )}
 </div>
 )
}
