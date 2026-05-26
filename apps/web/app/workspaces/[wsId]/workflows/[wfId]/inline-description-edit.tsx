'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { ProposeDescriptionEdit } from './propose-description-edit'
import { updateDescriptionAction } from './settings/actions'

interface Props {
 wsId: string
 wfId: string
 description: string
}

// Inline-editable description block for the workflow Overview + Spec
// surfaces. Read mode renders the description (first paragraph + a
// collapsible <details> for the rest) with a small Edit button in the
// corner; Edit mode swaps the body for a textarea + Save / Cancel.
// Save calls the existing `updateDescriptionAction` (single-sourced
// with the Settings page form) and refreshes the route so the layout
// title and sidebar pick up the new value.
export function InlineDescriptionBlock({ wsId, wfId, description }: Props) {
 const router = useRouter()
 const [editing, setEditing] = useState(false)
 const [draft, setDraft] = useState(description)
 const [isPending, startTransition] = useTransition()
 const [error, setError] = useState<string | null>(null)

 const dirty = draft.trim() !== description.trim()
 const paragraphs = description
 .split(/\n\s*\n/)
 .map((p) => p.trim() )
 .filter((p) => p.length > 0)
 if (paragraphs.length === 0 && !editing) return null
 const [first, ...rest] = paragraphs

 function handleSave() {
 setError(null)
 startTransition(async () => {
 const result = await updateDescriptionAction({
 wsId,
 wfId,
 description: draft,
 })
 if (!result.ok) {
 setError(result.error)
 return
 }
 setEditing(false)
 router.refresh })
 }

 function handleCancel() {
 setDraft(description)
 setEditing(false)
 setError(null)
 }

 return (
 <section
 style={{
 background: 'var(--bg)',
 border: '1px solid var(--border)',
 borderLeft: '3px solid var(--accent)',
 borderRadius: 8,
 padding: '14px 18px',
 marginBottom: 16,
 position: 'relative',
 }}
 >
 {!editing ? (
 <>
 <div className="inline-desc-actions-row">
 <button
 type="button"
 onClick={() => setEditing(true)}
 className="inline-desc-edit-btn"
 aria-label="Quick edit (cosmetic, direct save)"
 title="Quick edit · cosmetic, direct save"
 >
 Quick edit
 </button>
 <ProposeDescriptionEdit
 wsId={wsId}
 wfId={wfId}
 current={description}
 />
 </div>
 <p
 style={{
 fontSize: 14,
 lineHeight: 1.6,
 color: 'var(--text-2)',
 margin: 0,
 paddingRight: 64,
 }}
 >
 {first}
 </p>
 {rest.length > 0 ? (
 <details style={{ marginTop: 10 }}>
 <summary
 style={{
 cursor: 'pointer',
 fontSize: 12,
 color: 'var(--text-muted)',
 userSelect: 'none',
 }}
 >
 More context ({rest.length} more paragraph
 {rest.length === 1 ? '' : 's'})
 </summary>
 <div style={{ marginTop: 10 }}>
 {rest.map((p, i) => (
 <p
 key={i}
 style={{
 fontSize: 13.5,
 lineHeight: 1.6,
 color: 'var(--text-3)',
 margin: i === 0 ? 0 : '10px 0 0',
 }}
 >
 {p}
 </p>
 ))}
 </div>
 </details>
 ) : null}
 </>
 ) : (
 <>
 <textarea
 value={draft}
 onChange={(e) => setDraft(e.target.value)}
 disabled={isPending}
 rows={Math.max(4, Math.min(12, draft.split('\n').length + 1))}
 className="inline-desc-textarea"
 placeholder="Describe what this workflow does in plain language..."
 />
 <div className="inline-desc-meta">
 {draft.length} chars · min 10. Edits are cosmetic — they do
 not regenerate the spec, agent environment, eval cases, or metric.
 </div>
 <div className="inline-desc-actions">
 <button
 type="button"
 onClick={handleSave}
 disabled={isPending || !dirty || draft.trim().length < 10}
 className="btn btn-primary"
 >
 {isPending ? 'Saving…' : 'Save'}
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
 <p role="alert" className="inline-desc-error">
 {error}
 </p>
 ) : null}
 </>
 )}
 </section>
 )
}
