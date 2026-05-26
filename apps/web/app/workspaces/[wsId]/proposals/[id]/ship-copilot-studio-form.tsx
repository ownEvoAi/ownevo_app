'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { shipCopilotStudioAction } from './actions'

// "Deliver fix to Copilot Studio" action, shown on deployed skill proposals.
//
// Microsoft exposes no fix-feedback API, so this does not push anything: it
// returns the plain-language summary + the new instruction text for the
// reviewer to paste into Copilot Studio by hand, and records the delivery
// to the audit chain. The button is offered for any deployed skill
// proposal; the kernel enforces the real precondition (workflow must be
// Copilot Studio-origin) and returns a clear 4xx surfaced inline.
export function ShipCopilotStudioForm({
 proposalId,
 wsId,
 demoMode = false,
}: {
 proposalId: string
 wsId: string
 demoMode?: boolean
}) {
 const router = useRouter const [isPending, startTransition] = useTransition const [error, setError] = useState<string | null>(null)
 const [delivered, setDelivered] = useState<{
 summary: string
 instructionText: string
 already: boolean
 } | null>(null)

 function handle {
 setError(null)
 startTransition(async => {
 const result = await shipCopilotStudioAction({ proposalId, wsId })
 if (!result.ok) {
 setError(result.error)
 return
 }
 setDelivered({
 summary: result.summary,
 instructionText: result.instructionText,
 already: result.alreadyDelivered,
 })
 router.refresh })
 }

 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Deliver fix to Copilot Studio</div>
 <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
 Microsoft has no fix-feedback API, so this produces a plain-language diff
 to apply by hand in Copilot Studio and records the delivery to the audit
 chain. The workflow must be Copilot Studio-originated.
 </p>

 <button
 type="button"
 onClick={handle}
 disabled={isPending || demoMode || delivered !== null}
 title={demoMode ? 'Disabled in read-only demo' : undefined}
 className="btn btn-primary"
 style={{
 width: '100%',
 justifyContent: 'center',
 padding: '8px 14px',
 fontSize: 13,
 marginTop: 12,
 }}
 >
 {isPending ? 'Preparing…' : 'Deliver fix to Copilot Studio'}
 </button>

 {delivered && (
 <div style={{ marginTop: 12 }}>
 <p style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5 }}>
 {delivered.already ? 'Already delivered. ' : 'Recorded. '}
 Apply this update in Copilot Studio:
 </p>
 <p style={{ fontSize: 12.5, marginTop: 8, lineHeight: 1.5 }}>{delivered.summary}</p>
 <pre
 style={{
 fontSize: 11.5,
 whiteSpace: 'pre-wrap',
 wordBreak: 'break-word',
 background: 'var(--bg-subtle, var(--bg))',
 border: '1px solid var(--border)',
 borderRadius: 6,
 padding: 10,
 marginTop: 8,
 }}
 >
 {delivered.instructionText}
 </pre>
 </div>
 )}

 {error && (
 <p
 role="alert"
 style={{ fontSize: 12, color: 'var(--danger, #c0392b)', marginTop: 10, lineHeight: 1.5 }}
 >
 {error}
 </p>
 )}
 </div>
 )
}
