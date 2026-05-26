'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { shipLangSmithAction } from './actions'

// "Ship fix to LangSmith" action, shown on deployed skill proposals.
//
// The button is offered for any deployed skill proposal; the kernel
// enforces the real preconditions (workflow must be LangSmith-origin,
// the skill must be prompt-bound, a credential must be configured) and
// returns a clear 4xx that we surface inline — so the page doesn't have
// to pre-fetch all the gating state just to decide whether to render.
export function ShipLangSmithForm({
 proposalId,
 wsId,
 demoMode = false,
}: {
 proposalId: string
 wsId: string
 demoMode?: boolean
}) {
 const router = useRouter const [isPending, startTransition] = useTransition const [error, setError] = useState<string | null>(null)
 const [commitUrl, setCommitUrl] = useState<string | null>(null)
 const [alreadyShipped, setAlreadyShipped] = useState(false)

 function handle {
 setError(null)
 startTransition(async => {
 const result = await shipLangSmithAction({ proposalId, wsId })
 if (!result.ok) {
 setError(result.error)
 return
 }
 setCommitUrl(result.commitUrl)
 setAlreadyShipped(result.alreadyShipped)
 router.refresh })
 }

 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Ship fix to LangSmith</div>
 <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
 Pushes this deployed fix back to the customer&apos;s LangSmith workspace
 as a new prompt version. The workflow must be LangSmith-originated and
 the skill bound to a prompt; configure the API key in Settings →
 Integrations.
 </p>

 <button
 type="button"
 onClick={handle}
 disabled={isPending || demoMode || commitUrl !== null}
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
 {isPending ? 'Shipping…' : 'Ship fix to LangSmith'}
 </button>

 {commitUrl && (
 <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 10, lineHeight: 1.5 }}>
 {alreadyShipped ? 'Already shipped — ' : 'Shipped. '}
 <a href={commitUrl} target="_blank" rel="noopener noreferrer">
 View commit in LangSmith
 </a>
 </p>
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
