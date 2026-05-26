'use client'

import { useActionState } from 'react'
import { useFormStatus } from 'react-dom'
import {
 pushEvalCasesCopilotStudioAction,
 type PushEvalCasesActionState,
} from './actions'

const initialState: PushEvalCasesActionState = { error: null, result: null }

export function PushEvalCasesCopilotStudioButton({
 wsId,
 wfId,
}: {
 wsId: string
 wfId: string
}) {
 const action = pushEvalCasesCopilotStudioAction.bind(null, wsId, wfId)
 const [state, formAction] = useActionState(action, initialState)

 return (
 <details className="push-cs">
 <summary className="btn btn-secondary" style={{ cursor: 'pointer' }}>
 Push to Copilot Studio &rsaquo;
 </summary>
 <form
 action={formAction}
 style={{
 display: 'flex',
 flexDirection: 'column',
 gap: 8,
 marginTop: 8,
 padding: 12,
 background: 'var(--bg)',
 border: '1px solid var(--border)',
 borderRadius: 8,
 minWidth: 280,
 }}
 >
 <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>
 Copilot Studio agent id
 <input
 name="agent_id"
 required
 placeholder="e.g. a1b2c3d4-…"
 style={{ width: '100%', marginTop: 4 }}
 />
 </label>
 <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>
 Test set name (optional)
 <input
 name="test_set_name"
 placeholder="ownEvo · <workflow>"
 style={{ width: '100%', marginTop: 4 }}
 />
 </label>
 <label
 style={{ fontSize: 12, color: 'var(--text-muted)', display: 'flex', gap: 6 }}
 >
 <input type="checkbox" name="test_fold_only" />
 Push held-out (test-fold) cases only
 </label>
 <SubmitButton />
 {state.error ? (
 <div role="alert" className="api-banner">
 <strong>Push failed.</strong> {state.error}
 </div>
 ) : null}
 {state.result && !state.error ? (
 <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
 Pushed {state.result.caseCount} case
 {state.result.caseCount === 1 ? '' : 's'} as test set{' '}
 <code>{state.result.testSetId || '(id not returned)'}</code>.
 </p>
 ) : null}
 </form>
 </details>
 )
}

function SubmitButton {
 const { pending } = useFormStatus return (
 <button type="submit" className="btn btn-primary" disabled={pending} aria-disabled={pending}>
 {pending ? (
 <>
 <span className="spinner" aria-hidden /> Pushing eval cases…
 </>
 ) : (
 <>Push eval cases</>
 )}
 </button>
 )
}
