'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import type { SkillSummary } from '@/lib/api'
import { updateLangSmithBindingAction } from './actions'

// Manual LangSmith prompt-binding card. Lists the workflow's skills and
// lets the operator set the LangSmith Prompt Hub id each maps to — used
// when auto-binding (from ingested spans) didn't fire, or to push a
// greenfield skill to a new prompt. The current binding is visible on
// the skill's detail page; this card is the write surface.
export function LangSmithBindingForm({
 wsId,
 wfId,
 skills,
 demoMode = false,
}: {
 wsId: string
 wfId: string
 skills: SkillSummary[]
 demoMode?: boolean
}) {
 return (
 <div className="settings-card">
 <div className="settings-card-header">
 <h2 className="settings-card-title">LangSmith binding</h2>
 <p className="settings-card-subtitle">
 Maps each skill to a LangSmith Prompt Hub identifier so approved
 fixes can be shipped back as new prompt versions. Auto-populated from
 ingested traces when possible; set it here otherwise.
 </p>
 </div>
 {skills.length === 0 ? (
 <p className="settings-empty-state">This workflow has no skills yet.</p>
 ) : (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
 {skills.map((s) => (
 <SkillBindingRow
 key={s.id}
 wsId={wsId}
 wfId={wfId}
 skillId={s.id}
 demoMode={demoMode}
 />
 ))}
 </div>
 )}
 </div>
 )
}

function SkillBindingRow({
 wsId,
 wfId,
 skillId,
 demoMode,
}: {
 wsId: string
 wfId: string
 skillId: string
 demoMode: boolean
}) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [promptId, setPromptId] = useState('')
 const [saved, setSaved] = useState(false)
 const [error, setError] = useState<string | null>(null)

 function save() {
 setError(null)
 setSaved(false)
 startTransition(async () => {
 const trimmed = promptId.trim()
 const r = await updateLangSmithBindingAction({
 wsId,
 wfId,
 skillId,
 promptId: trimmed === '' ? null : trimmed,
 })
 if (!r.ok) {
 setError(r.error)
 return
 }
 setSaved(true)
 router.refresh })
 }

 return (
 <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
 <label className="settings-label" htmlFor={`bind-${skillId}`}>
 <code>{skillId}</code>
 </label>
 <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
 <input
 id={`bind-${skillId}`}
 type="text"
 value={promptId}
 placeholder="LangSmith prompt id (blank clears)"
 onChange={(e) => setPromptId(e.target.value)}
 disabled={isPending || demoMode}
 style={{
 flex: 1,
 padding: '8px 10px',
 fontSize: 13,
 fontFamily: 'inherit',
 border: '1px solid var(--border)',
 borderRadius: 6,
 background: 'var(--bg)',
 color: 'var(--text)',
 }}
 />
 <button
 type="button"
 onClick={save}
 disabled={isPending || demoMode}
 className="btn"
 >
 {isPending ? 'Saving…' : 'Set'}
 </button>
 </div>
 {saved && (
 <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 4 }}>Saved.</p>
 )}
 {error && (
 <p role="alert" style={{ fontSize: 11.5, color: 'var(--danger, #c0392b)', marginTop: 4 }}>
 {error}
 </p>
 )}
 </div>
 )
}
