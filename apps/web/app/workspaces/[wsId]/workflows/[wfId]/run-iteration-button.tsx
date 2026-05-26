'use client'

import { useActionState } from 'react'
import { useFormStatus } from 'react-dom'
import { runIterationAction, type RunIterationState } from './actions'

const initialState: RunIterationState = {
 error: null,
 iterationIndex: null,
 valScore: null,
 nFailed: null,
 nCases: null,
 proposalId: null,
}

export function RunIterationButton({
 wsId,
 wfId,
 iterationCount,
}: {
 wsId: string
 wfId: string
 iterationCount: number
}) {
 const action = runIterationAction.bind(null, wsId, wfId)
 const [state, formAction] = useActionState(action, initialState)

 return (
 <form action={formAction} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
 <SubmitButton iterationCount={iterationCount} />
 {state.error ? (
 <div role="alert" className="api-banner" style={{ marginTop: 8 }}>
 <strong>Iteration failed.</strong> {state.error}
 </div>
 ) : null}
 {state.iterationIndex !== null && !state.error ? (
 <div className="iteration-result-card">
 <div>
 <strong>Iteration {state.iterationIndex} complete.</strong>{' '}
 val_score: <code>{state.valScore?.toFixed(3)}</code> ·{' '}
 {state.nFailed}/{state.nCases} cases failed
 </div>
 {state.proposalId ? (
 <a
 href={`/workspaces/${wsId}/proposals/${state.proposalId}`}
 className="btn btn-secondary"
 style={{ marginTop: 8 }}
 >
 Review proposal &rsaquo;
 </a>
 ) : (
 <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '4px 0 0' }}>
 No proposal generated this round (proposer found no improvement
 vector). Run again to try a different angle.
 </p>
 )}
 <a
 href={`/workspaces/${wsId}/workflows/${wfId}/proposals`}
 style={{
 fontSize: 12,
 color: 'var(--accent)',
 marginTop: 8,
 display: 'inline-block',
 }}
 >
 View all proposals →
 </a>
 </div>
 ) : null}
 </form>
 )
}

function SubmitButton({ iterationCount }: { iterationCount: number }) {
 const { pending } = useFormStatus return (
 <button
 type="submit"
 className="btn btn-primary"
 disabled={pending}
 aria-disabled={pending}
 >
 {pending ? (
 <>
 <span className="spinner" aria-hidden /> Running iteration…
 </>
 ) : iterationCount === 0 ? (
 <>Run first iteration &rsaquo;</>
 ) : (
 <>+ Run iteration #{iterationCount + 1}</>
 )}
 </button>
 )
}
