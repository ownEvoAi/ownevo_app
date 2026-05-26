'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import {
 createMetricProposal,
 KernelApiError,
 type MetricDefinitionShape,
} from '@/lib/api'

interface Props {
 wsId: string
 wfId: string
 current: MetricDefinitionShape | null
}

// 9.2.3 — Propose-edit affordance for the success metric on the
// workflow Spec tab. Read mode shows a small "Propose edit" button;
// edit mode swaps the SectionShell body for a compact form
// (name / family / direction / description + a plain-language
// summary required for the proposal queue). Submit POSTs to
// `/api/workflows/{wfId}/proposals/metric` and redirects to the
// resulting proposal detail page where a reviewer approves the
// change.
export function ProposeMetricEdit({ wsId, wfId, current }: Props) {
 const router = useRouter const [editing, setEditing] = useState(false)
 const [isPending, startTransition] = useTransition const [error, setError] = useState<string | null>(null)

 const [name, setName] = useState(current?.name ?? '')
 const [family, setFamily] = useState(current?.family ?? '')
 const [direction, setDirection] = useState(current?.direction ?? 'higher')
 const [description, setDescription] = useState(current?.description ?? '')
 const [summary, setSummary] = useState('')
 const [rationale, setRationale] = useState('')

 function reset {
 setName(current?.name ?? '')
 setFamily(current?.family ?? '')
 setDirection(current?.direction ?? 'higher')
 setDescription(current?.description ?? '')
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
 if (name.trim.length === 0) {
 setError('Metric name is required.')
 return
 }
 startTransition(async => {
 try {
 const proposal = await createMetricProposal(wfId, {
 plain_language_summary: summary.trim ,
 proposed_metric: {
 name: name.trim ,
 family: family.trim || null,
 direction: direction.trim || null,
 description: description.trim || null,
 },
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
 className="btn btn-secondary propose-edit-btn"
 >
 Propose edit
 </button>
 )
 }

 return (
 <div className="propose-edit-panel">
 <div className="propose-edit-head">
 <strong>Propose metric edit</strong>
 <span className="propose-edit-help">
 Goes through proposal review · regression gate ·
 domain-expert approval.
 </span>
 </div>
 <div className="propose-edit-grid">
 <label className="propose-edit-field">
 <span>Name</span>
 <input
 value={name}
 onChange={(e) => setName(e.target.value)}
 disabled={isPending}
 />
 </label>
 <label className="propose-edit-field">
 <span>Family</span>
 <input
 value={family}
 onChange={(e) => setFamily(e.target.value)}
 placeholder="e.g. classification, regression"
 disabled={isPending}
 />
 </label>
 <label className="propose-edit-field">
 <span>Direction</span>
 <select
 value={direction}
 onChange={(e) => setDirection(e.target.value)}
 disabled={isPending}
 >
 <option value="higher">higher is better</option>
 <option value="lower">lower is better</option>
 </select>
 </label>
 </div>
 <label className="propose-edit-field">
 <span>Description</span>
 <textarea
 value={description}
 onChange={(e) => setDescription(e.target.value)}
 rows={3}
 disabled={isPending}
 />
 </label>
 <label className="propose-edit-field">
 <span>
 Plain-language summary <em>(shown in the proposal queue)</em>
 </span>
 <input
 value={summary}
 onChange={(e) => setSummary(e.target.value)}
 placeholder="e.g. Switch from F1 to recall — recall-first."
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
 disabled={isPending}
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
