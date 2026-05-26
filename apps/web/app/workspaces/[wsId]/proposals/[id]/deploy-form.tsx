'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { deployAction } from './actions'

// Renders the appropriate action button based on the proposal's
// current state — Deploy when 'approved-awaiting-deploy', Rollback
// when 'deployed'. Both routes go through the same Server Action with
// an `action` discriminator. Reviewer identity is stored in local
// state, shared with the parent Decide form for the demo scaffold
// .
export function DeployForm({
 proposalId,
 wsId,
 workflowId,
 state,
 kind = 'skill',
 demoMode = false,
}: {
 proposalId: string
 wsId: string
 workflowId: string
 state: 'approved-awaiting-deploy' | 'deployed'
 kind?: 'skill' | 'description' | 'metric' | 'sim' | 'ui-primitive'
 demoMode?: boolean
}) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [decidedBy, setDecidedBy] = useState('human:reviewer')
 const [error, setError] = useState<string | null>(null)

 const isDeploy = state === 'approved-awaiting-deploy'
 const actionLabel = isDeploy ? 'Deploy' : 'Roll back'
 // 9.2.3 — non-skill kinds write directly to the workflow row; the
 // help text differs from the skill flow because there's no separate
 // skill_versions pointer to flip.
 const helpText = isDeploy
 ? kind === 'skill'
 ? "Sets the skill's deployed_version_id to this proposal's version. Future workflow runs use this instruction."
 : `Applies the proposed ${kind} change to the workflow row. The change is live as soon as Deploy fires.`
 : kind === 'skill'
 ? 'Reverts the deployed_version_id. The skill returns to its previous deployed version (or null if this was the first deployment).'
 : `Rollback for non-skill artifact proposals isn't supported yet — create a new ${kind} proposal pointing at the previous value to revert.`

 function handle() {
 setError(null)
 startTransition(async () => {
 const result = await deployAction({
 proposalId,
 wsId,
 workflowId,
 action: isDeploy ? 'deploy' : 'rollback',
 decidedBy,
 })
 if (!result.ok) {
 setError(result.error)
 return
 }
 router.refresh })
 }

 return (
 <div className="sidebar-card">
 <div className="sidebar-title">{isDeploy ? 'Deploy' : 'Rollback'}</div>
 <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
 {helpText}
 </p>

 <input
 type="text"
 value={decidedBy}
 onChange={(e) => setDecidedBy(e.target.value)}
 disabled={isPending}
 aria-label="Reviewer identity"
 style={{
 marginTop: 10,
 width: '100%',
 padding: '8px 10px',
 fontSize: 13,
 fontFamily: 'inherit',
 border: '1px solid var(--border)',
 borderRadius: 6,
 background: 'var(--bg)',
 color: 'var(--text)',
 }}
 />
 <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
 Reviewer · scaffold
 </div>

 <button
 type="button"
 onClick={handle}
 disabled={isPending || demoMode}
 title={demoMode ? 'Disabled in read-only demo' : undefined}
 className={isDeploy ? 'btn btn-primary' : 'btn btn-danger'}
 style={{
 width: '100%',
 justifyContent: 'center',
 padding: '8px 14px',
 fontSize: 13,
 marginTop: 12,
 }}
 >
 {isPending
 ? `${actionLabel === 'Deploy' ? 'Deploying' : 'Rolling back'}…`
 : `${actionLabel} this version`}
 </button>

 {demoMode && (
 <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 10, lineHeight: 1.4 }}>
 {actionLabel} is disabled in this read-only demo.
 </p>
 )}

 {error && (
 <p
 role="alert"
 style={{
 fontSize: 12,
 color: '#b42318',
 marginTop: 10,
 background: 'rgba(239, 68, 68, 0.08)',
 padding: '8px 10px',
 borderRadius: 5,
 }}
 >
 {error}
 </p>
 )}
 </div>
 )
}
