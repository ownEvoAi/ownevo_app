'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import type { LangSmithStatus } from '@/lib/api'
import {
 deleteLangSmithKeyAction,
 saveLangSmithKeyAction,
 testLangSmithKeyAction,
} from './actions'

// Client island for the LangSmith credential card. The key is write-only
// from the UI's perspective — the server never returns it, so the input
// is always empty on load and "configured" is shown via the status line.
export function LangSmithForm({
 wsId,
 initialStatus,
 demoMode = false,
}: {
 wsId: string
 initialStatus: LangSmithStatus
 demoMode?: boolean
}) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [apiKey, setApiKey] = useState('')
 const [error, setError] = useState<string | null>(null)
 const [testMsg, setTestMsg] = useState<string | null>(null)

 function save() {
 setError(null)
 setTestMsg(null)
 startTransition(async () => {
 const r = await saveLangSmithKeyAction(wsId, apiKey)
 if (!r.ok) {
 setError(r.error)
 return
 }
 setApiKey('')
 router.refresh })
 }

 function test() {
 setError(null)
 setTestMsg(null)
 startTransition(async () => {
 const r = await testLangSmithKeyAction(wsId)
 if (!r.ok) {
 setError(r.error)
 return
 }
 setTestMsg(
 r.status === 'ok'
 ? 'Connection OK — the key authenticates.'
 : r.status === 'invalid'
 ? `Key rejected by LangSmith${r.detail ? `: ${r.detail}` : ''}`
 : `Could not validate${r.detail ? `: ${r.detail}` : ''}`,
 )
 router.refresh })
 }

 function remove() {
 setError(null)
 setTestMsg(null)
 startTransition(async () => {
 const r = await deleteLangSmithKeyAction(wsId)
 if (!r.ok) {
 setError(r.error)
 return
 }
 router.refresh })
 }

 return (
 <div className="settings-card">
 <div className="settings-card-header">
 <h2 className="settings-card-title">LangSmith</h2>
 <p className="settings-card-subtitle">
 Stores the LangSmith API key used to ship approved fixes back to a
 customer&apos;s workspace as new prompt versions. The key is encrypted
 at rest and never displayed again after saving.
 </p>
 </div>

 <p className="settings-label">
 Status:{' '}
 {initialStatus.configured ? (
 <strong>
 configured
 {initialStatus.validation_status
 ? ` · last test: ${initialStatus.validation_status}`
 : ' · not yet tested'}
 </strong>
 ) : (
 <strong>not configured</strong>
 )}
 </p>

 <label className="settings-label" htmlFor="langsmith-key">
 API key
 </label>
 <input
 id="langsmith-key"
 type="password"
 value={apiKey}
 placeholder={initialStatus.configured ? '•••••• (set — enter to replace)' : 'lsv2_pt_…'}
 onChange={(e) => setApiKey(e.target.value)}
 disabled={isPending || demoMode}
 autoComplete="off"
 style={{
 width: '100%',
 padding: '8px 10px',
 fontSize: 13,
 fontFamily: 'inherit',
 border: '1px solid var(--border)',
 borderRadius: 6,
 background: 'var(--bg)',
 color: 'var(--text)',
 marginTop: 4,
 }}
 />

 <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
 <button
 type="button"
 onClick={save}
 disabled={isPending || demoMode || !apiKey.trim() }
 className="btn btn-primary"
 >
 {isPending ? 'Saving…' : 'Save'}
 </button>
 <button
 type="button"
 onClick={test}
 disabled={isPending || demoMode || !initialStatus.configured}
 className="btn"
 >
 Test connection
 </button>
 {initialStatus.configured && (
 <button
 type="button"
 onClick={remove}
 disabled={isPending || demoMode}
 className="btn btn-danger"
 >
 Remove
 </button>
 )}
 </div>

 <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 10 }}>
 Get a key from LangSmith → Settings → API Keys.
 </p>

 {testMsg && (
 <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 10 }}>{testMsg}</p>
 )}
 {error && (
 <p role="alert" style={{ fontSize: 12.5, color: 'var(--danger, #c0392b)', marginTop: 10 }}>
 {error}
 </p>
 )}
 </div>
 )
}
