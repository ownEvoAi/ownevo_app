'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import type { ProviderModels } from '@/lib/api'
import { updateAgentModelAction } from './actions'

// Client island for the per-workflow agent-model picker.
//
// Provider+model catalog is fetched server-side in `page.tsx` and
// passed as `providers`. The dropdown renders one `<optgroup>` per
// enabled provider; each `<option>`'s value is the `provider:model`
// slug stored on `workflows.agent_model_id`. Server Action validates
// the slug against the same env-driven allowlist and appends a
// hash-chained audit entry on success.
export function ModelPickerForm({
 wsId,
 wfId,
 initialAgentModelId,
 providers,
 readOnly = false,
}: {
 wsId: string
 wfId: string
 initialAgentModelId: string
 providers: ProviderModels[]
 readOnly?: boolean
}) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [selected, setSelected] = useState(initialAgentModelId)
 const [savedAt, setSavedAt] = useState<string | null>(null)
 const [error, setError] = useState<string | null>(null)

 const dirty = selected !== initialAgentModelId
 const empty = providers.length === 0

 function handleSave() {
 setError(null)
 setSavedAt(null)
 startTransition(async () => {
 const result = await updateAgentModelAction({
 wsId,
 wfId,
 agentModelId: selected,
 })
 if (!result.ok) {
 setError(result.error)
 return
 }
 setSavedAt(selected)
 router.refresh })
 }

 function handleReset() {
 setSelected(initialAgentModelId)
 setError(null)
 setSavedAt(null)
 }

 return (
 <div className="settings-card">
 <div className="settings-card-header">
 <h2 className="settings-card-title">Agent model</h2>
 <p className="settings-card-subtitle">
 {readOnly
 ? 'Which LLM the agent solver uses for this workflow. The list below shows the available models — selection is view-only on this deployment.'
 : 'Which LLM the agent solver uses for this workflow. The list below is the union of providers your operator has enabled via  '
 }
 {!readOnly && <><code>OWNEVO_PROVIDER_*</code>{' '}environment variables. Every change is recorded in the append-only audit log.</>}
 </p>
 </div>

 {empty ? (
 <p className="settings-empty-state">
 No providers enabled. Set{' '}
 <code>OWNEVO_PROVIDER_&lt;NAME&gt;_ENABLED=true</code> +{' '}
 <code>OWNEVO_PROVIDER_&lt;NAME&gt;_MODELS=…</code> in{' '}
 <code>.env</code> and restart the kernel.
 </p>
 ) : (
 <>
 <label className="settings-label" htmlFor="agent-model-select">
 Model
 </label>
 <select
 id="agent-model-select"
 value={selected}
 onChange={(e) => {
 if (readOnly) return
 setSelected(e.target.value)
 if (savedAt) setSavedAt(null)
 }}
 disabled={isPending || readOnly}
 className="settings-select"
 >
 {/* Show the current selection even if it's no longer in the
 allowlist — keep the dropdown coherent for workflows
 whose model was disabled after the fact. */}
 {!providers.some((p) =>
 p.models.some((m) => `${p.id}:${m}` === selected),
 ) ? (
 <option value={selected}>
 {selected} (not in current allowlist)
 </option>
 ) : null}
 {providers.map((provider) => (
 <optgroup key={provider.id} label={provider.label}>
 {provider.models.map((model) => {
 const slug = `${provider.id}:${model}`
 return (
 <option key={slug} value={slug}>
 {model}
 </option>
 )
 })}
 </optgroup>
 ))}
 </select>
 <p className="settings-subnote">
 Selection takes effect on the next iteration. The agent solver
 dispatches through the chosen provider; the proposer remains on
 Anthropic.
 </p>
 </>
 )}

 {!readOnly && (
 <div className="settings-card-actions">
 <button
 type="button"
 onClick={handleSave}
 disabled={isPending || !dirty || empty}
 className="btn btn-primary"
 >
 {isPending ? 'Saving…' : 'Save'}
 </button>
 {dirty && !isPending ? (
 <button
 type="button"
 onClick={handleReset}
 className="btn btn-secondary"
 >
 Discard
 </button>
 ) : null}
 {savedAt && !dirty ? (
 <span className="settings-saved-cue">Saved.</span>
 ) : null}
 </div>
 )}

 {error ? (
 <p role="alert" className="settings-error">
 {error}
 </p>
 ) : null}
 </div>
 )
}
