'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { createUIPrimitiveProposal, KernelApiError } from '@/lib/api'

interface Props {
 wsId: string
 wfId: string
 current: Array<{ type: string; [k: string]: unknown }>
}

// Mirrors `agent_solver.PRIMITIVE_PAYLOAD_KEYS`. Kept in lockstep
// with the kernel-side list — the agent only emits payloads for
// these types, and the operate resolver only knows how to render
// these types. Adding a new primitive here without the kernel-side
// counterpart would render an empty operate tab.
const KNOWN_PRIMITIVE_TYPES: ReadonlyArray<string> = [
 'HeadlineMetrics',
 'TimeSeriesChart',
 'TableView',
 'AlertList',
 'KanbanBoard',
 'ScheduleGrid',
 'ConversationView',
]

// 9.2.3 — Propose-edit affordance for the Operate-view UI primitive
// list on the workflow Spec tab. Read mode shows a small button;
// edit mode swaps in a compact form: one row of checkboxes (the
// known primitive types) + a required plain-language summary +
// an optional rationale. Submit POSTs to the create-ui-primitive
// endpoint and redirects to the resulting proposal detail page.
//
// Per-primitive props (titles, column lists, etc.) survive untouched
// when a type stays checked; types being added arrive with only the
// `type` field set — the agent-solver layer populates payload shape
// at run-time, not here.
export function ProposeUIPrimitiveEdit({ wsId, wfId, current }: Props) {
 const router = useRouter()
 const currentTypes = current.map((p) => p.type)
 const [editing, setEditing] = useState(false)
 const [selected, setSelected] = useState<Set<string>>(() => new Set(currentTypes),
 )
 const [summary, setSummary] = useState('')
 const [rationale, setRationale] = useState('')
 const [isPending, startTransition] = useTransition()
 const [error, setError] = useState<string | null>(null)

 const toggle = (t: string) => {
 setSelected((prev) => {
 const next = new Set(prev)
 if (next.has(t)) next.delete(t)
 else next.add(t)
 return next
 })
 }

 const dirty = (() => {
 if (selected.size !== currentTypes.length) return true
 for (const t of currentTypes) if (!selected.has(t)) return true
 return false
 })()
 function reset() {
 setSelected(new Set(currentTypes))
 setSummary('')
 setRationale('')
 setError(null)
 }

 function handleCancel() {
 setEditing(false)
 reset()
 }

 function handleSubmit() {
 setError(null)
 if (summary.trim().length === 0) {
 setError('Plain-language summary is required.')
 return
 }
 if (selected.size === 0) {
 setError('At least one primitive must be selected.')
 return
 }
 // Preserve existing props for retained types; new types arrive
 // with just `type`. The kernel post-approval write will merge
 // these into spec.ui.tabs[0].primitives.
 const byType = new Map(current.map((p) => [p.type, p]))
 const proposed = Array.from(selected).map(
 (t) => byType.get(t) ?? { type: t },
 )
 startTransition(async () => {
 try {
 const proposal = await createUIPrimitiveProposal(wfId, {
 plain_language_summary: summary.trim() ,
 proposed_primitives: proposed,
 rationale: rationale.trim() || null,
 })
 setEditing(false)
 reset()
 router.refresh()
 router.push(`/workspaces/${wsId}/proposals/${proposal.id}`)
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
 onClick={() => setEditing(true)}
 className="btn btn-secondary propose-edit-btn"
 >
 Propose edit
 </button>
 )
 }

 return (
 <div className="propose-edit-panel">
 <div className="propose-edit-head">
 <strong>Propose primitive-list edit</strong>
 <span className="propose-edit-help">
 Goes through proposal review · regression gate ·
 domain-expert approval.
 </span>
 </div>

 <div className="propose-ui-checkbox-grid">
 {KNOWN_PRIMITIVE_TYPES.map((t) => (
 <label
 key={t}
 className={`propose-ui-checkbox${
 selected.has(t) ? ' selected' : ''
 }`}
 >
 <input
 type="checkbox"
 checked={selected.has(t)}
 onChange={() => toggle(t)}
 disabled={isPending}
 />
 <span className="propose-ui-checkbox-label">{t}</span>
 </label>
 ))}
 </div>

 <label className="propose-edit-field">
 <span>
 Plain-language summary <em>(shown in the proposal queue)</em>
 </span>
 <input
 value={summary}
 onChange={(e) => setSummary(e.target.value)}
 placeholder="e.g. Add AlertList to surface high-severity alerts on the Operate tab."
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
 disabled={isPending || !dirty}
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
