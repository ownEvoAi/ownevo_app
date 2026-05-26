'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { createDescriptionProposal, KernelApiError } from '@/lib/api'

interface Props {
 wsId: string
 wfId: string
 current: string
}

// 9.2.3 — gate-routed description edit. The inline edit on the
// description block stays as the "Quick edit" path (direct PATCH,
// cosmetic). This button opens a separate form that creates a
// kind='description' proposal — the path for substantive rewrites
// that need a domain expert's approval before they ship.
export function ProposeDescriptionEdit({ wsId, wfId, current }: Props) {
 const router = useRouter const [editing, setEditing] = useState(false)
 const [draft, setDraft] = useState(current)
 const [summary, setSummary] = useState('')
 const [rationale, setRationale] = useState('')
 const [isPending, startTransition] = useTransition const [error, setError] = useState<string | null>(null)

 const dirty = draft.trim !== current.trim function reset {
 setDraft(current)
 setSummary('')
 setRationale('')
 setError(null)
 }

 function handleCancel {
 setEditing(false)
 reset }

 function handleSubmit {
 setError(null)
 if (summary.trim.length === 0) {
 setError('Plain-language summary is required.')
 return
 }
 if (draft.trim.length < 10) {
 setError('Proposed description must be at least 10 characters.')
 return
 }
 startTransition(async => {
 try {
 const proposal = await createDescriptionProposal(wfId, {
 plain_language_summary: summary.trim ,
 proposed_description: draft.trim ,
 rationale: rationale.trim || null,
 })
 setEditing(false)
 reset router.refresh router.push(`/workspaces/${wsId}/proposals/${proposal.id}`)
 } catch (err) {
 if (err instanceof KernelApiError) {
 setError(err.detail || err.message)
 } else {
 setError(err instanceof Error ? err.message : 'Unknown error')
 }
 }
 })
 }

 if (!editing) {
 return (
 <button
 type="button"
 onClick={ => setEditing(true)}
 className="inline-desc-propose-btn"
 aria-label="Propose substantive change to description"
 >
 Propose change
 </button>
 )
 }

 return (
 <div className="propose-edit-panel propose-description-panel">
 <div className="propose-edit-head">
 <strong>Propose description change</strong>
 <span className="propose-edit-help">
 Substantive rewrite · goes through proposal review +
 domain-expert approval. Use Quick edit (Edit button) for
 typos or cosmetic tweaks.
 </span>
 </div>
 <textarea
 value={draft}
 onChange={(e) => setDraft(e.target.value)}
 disabled={isPending}
 rows={Math.max(6, Math.min(16, draft.split('\n').length + 1))}
 className="inline-desc-textarea"
 placeholder="Describe what this workflow does in plain language..."
 />
 <div className="inline-desc-meta">
 {draft.length} chars · min 10
 </div>
 <label className="propose-edit-field">
 <span>
 Plain-language summary <em>(shown in the proposal queue)</em>
 </span>
 <input
 value={summary}
 onChange={(e) => setSummary(e.target.value)}
 placeholder="e.g. Add 2024 holiday markdowns to past misses."
 disabled={isPending}
 />
 </label>
 <label className="propose-edit-field">
 <span>
 Rationale <em>(optional · why this change)</em>
 </span>
 <textarea
 value={rationale}
 onChange={(e) => setRationale(e.target.value)}
 rows={2}
 disabled={isPending}
 />
 </label>
 <div className="propose-edit-actions">
 <button
 type="button"
 onClick={handleSubmit}
 disabled={isPending || !dirty || draft.trim.length < 10}
 className="btn btn-primary"
 >
 {isPending ? 'Creating…' : 'Create proposal'}
 </button>
 <button
 type="button"
 onClick={handleCancel}
 disabled={isPending}
 className="btn btn-secondary"
 >
 Cancel
 </button>
 </div>
 {error ? (
 <p role="alert" className="propose-edit-error">
 {error}
 </p>
 ) : null}
 </div>
 )
}
