'use client'

import { useState, useTransition } from 'react'
import { reviseWorkflowAction } from './actions'

// Revise = "delete this generated workflow and go back to describe."
// Two-click confirm so a stray click can't drop the row. The kernel
// cascade is final — no undo — so the inline confirm carries weight.
export function ReviseButton({ wsId, wfId }: { wsId: string; wfId: string }) {
 const [confirming, setConfirming] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [isPending, startTransition] = useTransition()
 if (!confirming) {
 return (
 <button
 type="button"
 className="btn btn-secondary"
 onClick={() => setConfirming(true)}
 >
 Revise &mdash; this isn&rsquo;t quite right
 </button>
 )
 }

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
 <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
 This deletes the generated spec. Confirm?
 </span>
 <div style={{ display: 'flex', gap: 8 }}>
 <button
 type="button"
 className="btn btn-secondary"
 onClick={() => {
 setConfirming(false)
 setError(null)
 }}
 disabled={isPending}
 >
 Keep
 </button>
 <button
 type="button"
 className="btn btn-danger"
 disabled={isPending}
 aria-disabled={isPending}
 onClick={() => {
 startTransition(async () => {
 const result = await reviseWorkflowAction({ wsId, wfId })
 if (result && result.ok === false) {
 setError(result.error)
 }
 })
 }}
 >
 {isPending ? 'Deleting…' : 'Yes, delete and start over'}
 </button>
 </div>
 {error ? (
 <div role="alert" className="api-banner" style={{ marginTop: 6 }}>
 <strong>Delete failed.</strong> {error}
 </div>
 ) : null}
 </div>
 )
}
