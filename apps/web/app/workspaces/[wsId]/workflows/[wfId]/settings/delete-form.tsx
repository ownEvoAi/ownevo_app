'use client'

import { useState, useTransition } from 'react'
import { deleteWorkflowAction } from './actions'

// Danger card. We require the operator to retype the workflow id to
// arm the delete button, then call the Server Action. On success the
// Server Action redirects to Health, so we don't need to handle the
// post-success state here.
export function DeleteWorkflowForm({
 wsId,
 wfId,
}: {
 wsId: string
 wfId: string
}) {
 const [isPending, startTransition] = useTransition()
 const [confirmation, setConfirmation] = useState('')
 const [error, setError] = useState<string | null>(null)

 const armed = confirmation.trim() === wfId

 function handleDelete() {
 if (!armed) return
 setError(null)
 startTransition(async () => {
 const result = await deleteWorkflowAction({
 wsId,
 wfId,
 confirmation,
 })
 if (!result.ok) {
 setError(result.error)
 }
 // ok=true path is unreachable — the Server Action redirects.
 })
 }

 return (
 <div className="settings-card settings-card-danger">
 <div className="settings-card-header">
 <h2 className="settings-card-title">Delete workflow</h2>
 <p className="settings-card-subtitle">
 Removes the workflow and every iteration, proposal, trace, eval
 case, failure cluster, and skill version tied to it. Audit
 entries are kept (append-only); their references become
 dangling pointers.
 <br />
 <strong>This cannot be undone.</strong>
 </p>
 </div>

 <label className="settings-confirm-label">
 Type <code>{wfId}</code> to confirm:
 <input
 type="text"
 value={confirmation}
 onChange={(e) => {
 setConfirmation(e.target.value)
 if (error) setError(null)
 }}
 disabled={isPending}
 autoComplete="off"
 spellCheck={false}
 className="settings-confirm-input"
 placeholder={wfId}
 />
 </label>

 <div className="settings-card-actions">
 <button
 type="button"
 onClick={handleDelete}
 disabled={isPending || !armed}
 className="btn btn-danger"
 >
 {isPending ? 'Deleting…' : 'Delete workflow permanently'}
 </button>
 </div>

 {error ? (
 <p role="alert" className="settings-error">
 {error}
 </p>
 ) : null}
 </div>
 )
}
