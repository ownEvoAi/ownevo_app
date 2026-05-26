'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { deployAction } from './actions'

// Client island for the Deploy / Rollback affordance on the skill
// detail page. The actual mutation runs server-side via the
// `deployAction` Server Action — the client just collects the
// reviewer identity, dispatches, and triggers a router.refresh on
// success so the production pointer + button visibility re-renders.
//
// Rendering rules (from SkillDetail):
// * Show Deploy button iff `deployableProposalId` is non-null.
// * Show Rollback button iff `deployedProposalId` is non-null.
// * If both, show both — a deployable proposal can sit alongside a
// currently-deployed older one until the operator rolls back.

interface Props {
 wsId: string
 skillId: string
 deployableProposalId: string | null
 deployableProposalVersionSeq: number | null
 deployedProposalId: string | null
 deployedVersionSeq: number | null
}

export function DeployRollbackPanel({
 wsId,
 skillId,
 deployableProposalId,
 deployableProposalVersionSeq,
 deployedProposalId,
 deployedVersionSeq,
}: Props) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [decidedBy, setDecidedBy] = useState('human:operator')
 const [error, setError] = useState<string | null>(null)
 const [pendingAction, setPendingAction] = useState<
 'deploy' | 'rollback' | null
 >(null)

 if (!deployableProposalId && !deployedProposalId) {
 return null
 }

 function handle(action: 'deploy' | 'rollback') {
 const proposalId =
 action === 'deploy' ? deployableProposalId : deployedProposalId
 if (!proposalId) return
 setError(null)
 setPendingAction(action)
 startTransition(async () => {
 const result = await deployAction({
 proposalId,
 wsId,
 skillId,
 action,
 decidedBy,
 })
 if (!result.ok) {
 setError(result.error)
 setPendingAction(null)
 return
 }
 setPendingAction(null)
 router.refresh })
 }

 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Production</div>

 {deployedProposalId && deployedVersionSeq !== null ? (
 <p style={{ fontSize: 12.5, color: 'var(--text)', marginBottom: 12 }}>
 Currently live: <strong>v{deployedVersionSeq}</strong>
 </p>
 ) : (
 <p
 style={{
 fontSize: 12.5,
 color: 'var(--text-muted)',
 marginBottom: 12,
 }}
 >
 Nothing deployed yet — approve a gate-passed proposal and hit Deploy.
 </p>
 )}

 <input
 type="text"
 value={decidedBy}
 onChange={(e) => setDecidedBy(e.target.value)}
 disabled={isPending}
 aria-label="Operator identity"
 style={{
 width: '100%',
 fontSize: 12,
 padding: '6px 8px',
 border: '1px solid var(--border)',
 borderRadius: 5,
 marginBottom: 10,
 background: 'var(--bg)',
 color: 'var(--text)',
 fontFamily: 'inherit',
 }}
 />

 <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
 {deployableProposalId && (
 <button
 type="button"
 onClick={() => handle('deploy')}
 disabled={isPending}
 className="btn btn-primary"
 style={{
 width: '100%',
 justifyContent: 'center',
 padding: '8px 12px',
 fontSize: 13,
 }}
 >
 {pendingAction === 'deploy'
 ? 'Deploying…'
 : deployableProposalVersionSeq !== null
 ? `Deploy v${deployableProposalVersionSeq}`
 : 'Deploy approved proposal'}
 </button>
 )}
 {deployedProposalId && (
 <button
 type="button"
 onClick={() => handle('rollback')}
 disabled={isPending}
 className="btn btn-ghost"
 style={{
 width: '100%',
 justifyContent: 'center',
 padding: '8px 12px',
 fontSize: 13,
 color: 'var(--red, #dc2626)',
 }}
 >
 {pendingAction === 'rollback' ? 'Rolling back…' : 'Rollback'}
 </button>
 )}
 </div>

 {error && (
 <p
 role="alert"
 style={{
 fontSize: 11.5,
 color: 'var(--red, #dc2626)',
 marginTop: 10,
 background: 'rgba(239, 68, 68, 0.08)',
 padding: '6px 8px',
 borderRadius: 5,
 lineHeight: 1.4,
 }}
 >
 {error}
 </p>
 )}

 <p
 style={{
 fontSize: 11,
 color: 'var(--text-muted)',
 marginTop: 12,
 lineHeight: 1.4,
 }}
 >
 Deploy moves the production pointer to the approved version.
 Rollback reverts to the prior deployment (or clears the pointer).
 Both actions are recorded in the audit log.
 </p>
 </div>
 )
}
