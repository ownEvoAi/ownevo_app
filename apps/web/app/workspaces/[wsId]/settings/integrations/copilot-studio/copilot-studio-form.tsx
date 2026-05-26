'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import type { CopilotStudioStatus } from '@/lib/api'
import {
 deleteCopilotStudioCredentialAction,
 saveCopilotStudioCredentialAction,
 testCopilotStudioConnectionAction,
} from './actions'

const inputStyle = {
 width: '100%',
 padding: '8px 10px',
 fontSize: 13,
 fontFamily: 'inherit',
 border: '1px solid var(--border)',
 borderRadius: 6,
 background: 'var(--bg)',
 color: 'var(--text)',
 marginTop: 4,
} as const

// Client island for the Copilot Studio credential card. The credential is
// write-only from the UI's perspective — the server stores it encrypted as
// a single blob and never returns any field, so every input is empty on
// load and "configured" is shown via the status line.
export function CopilotStudioForm({
 wsId,
 initialStatus,
 demoMode = false,
}: {
 wsId: string
 initialStatus: CopilotStudioStatus
 demoMode?: boolean
}) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [tenantId, setTenantId] = useState('')
 const [clientId, setClientId] = useState('')
 const [clientSecret, setClientSecret] = useState('')
 const [environmentUrl, setEnvironmentUrl] = useState('')
 const [authorityHost, setAuthorityHost] = useState('')
 const [error, setError] = useState<string | null>(null)
 const [testMsg, setTestMsg] = useState<string | null>(null)

 const canSave =
 !!tenantId.trim() && !!clientId.trim() && !!clientSecret.trim() && !!environmentUrl.trim()
 function save() {
 setError(null)
 setTestMsg(null)
 startTransition(async () => {
 const r = await saveCopilotStudioCredentialAction(wsId, {
 tenant_id: tenantId,
 client_id: clientId,
 client_secret: clientSecret,
 environment_url: environmentUrl,
 authority_host: authorityHost || null,
 })
 if (!r.ok) {
 setError(r.error)
 return
 }
 setTenantId('')
 setClientId('')
 setClientSecret('')
 setEnvironmentUrl('')
 setAuthorityHost('')
 router.refresh })
 }

 function test() {
 setError(null)
 setTestMsg(null)
 startTransition(async () => {
 const r = await testCopilotStudioConnectionAction(wsId)
 if (!r.ok) {
 setError(r.error)
 return
 }
 setTestMsg(
 r.status === 'ok'
 ? 'Connection OK — the service principal authenticates.'
 : r.status === 'invalid'
 ? `Service principal rejected${r.detail ? `: ${r.detail}` : ''}`
 : `Could not validate${r.detail ? `: ${r.detail}` : ''}`,
 )
 router.refresh })
 }

 function remove() {
 setError(null)
 setTestMsg(null)
 startTransition(async () => {
 const r = await deleteCopilotStudioCredentialAction(wsId)
 if (!r.ok) {
 setError(r.error)
 return
 }
 router.refresh })
 }

 return (
 <div className="settings-card">
 <div className="settings-card-header">
 <h2 className="settings-card-title">Microsoft Copilot Studio</h2>
 <p className="settings-card-subtitle">
 Stores the Entra ID service-principal credential used to authenticate
 against your Power Platform environment — to push eval cases via the
 Evaluation API and export an agent&apos;s definition. The credential is
 encrypted at rest and never displayed again after saving.
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

 <label className="settings-label" htmlFor="cs-tenant">
 Tenant ID
 </label>
 <input
 id="cs-tenant"
 type="text"
 value={tenantId}
 placeholder={initialStatus.configured ? '•••••• (set — enter to replace)' : 'e.g. 00000000-0000-0000-0000-000000000000'}
 onChange={(e) => setTenantId(e.target.value)}
 disabled={isPending || demoMode}
 autoComplete="off"
 style={inputStyle}
 />

 <label className="settings-label" htmlFor="cs-client-id" style={{ marginTop: 12, display: 'block' }}>
 Client ID (app registration)
 </label>
 <input
 id="cs-client-id"
 type="text"
 value={clientId}
 placeholder="application (client) ID"
 onChange={(e) => setClientId(e.target.value)}
 disabled={isPending || demoMode}
 autoComplete="off"
 style={inputStyle}
 />

 <label className="settings-label" htmlFor="cs-client-secret" style={{ marginTop: 12, display: 'block' }}>
 Client secret
 </label>
 <input
 id="cs-client-secret"
 type="password"
 value={clientSecret}
 placeholder={initialStatus.configured ? '•••••• (set — enter to replace)' : 'client secret value'}
 onChange={(e) => setClientSecret(e.target.value)}
 disabled={isPending || demoMode}
 autoComplete="off"
 style={inputStyle}
 />

 <label className="settings-label" htmlFor="cs-env-url" style={{ marginTop: 12, display: 'block' }}>
 Environment URL
 </label>
 <input
 id="cs-env-url"
 type="text"
 value={environmentUrl}
 placeholder="https://org.crm.dynamics.com"
 onChange={(e) => setEnvironmentUrl(e.target.value)}
 disabled={isPending || demoMode}
 autoComplete="off"
 style={inputStyle}
 />

 <label className="settings-label" htmlFor="cs-authority" style={{ marginTop: 12, display: 'block' }}>
 Authority host <span style={{ color: 'var(--text-muted)' }}>(optional — sovereign clouds only)</span>
 </label>
 <input
 id="cs-authority"
 type="text"
 value={authorityHost}
 placeholder="https://login.microsoftonline.com"
 onChange={(e) => setAuthorityHost(e.target.value)}
 disabled={isPending || demoMode}
 autoComplete="off"
 style={inputStyle}
 />

 <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
 <button
 type="button"
 onClick={save}
 disabled={isPending || demoMode || !canSave}
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
 Register an app in Entra ID, grant it Power Platform permissions, and add it
 as an application user in your environment. The client secret is created under
 the app registration → Certificates &amp; secrets.
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
