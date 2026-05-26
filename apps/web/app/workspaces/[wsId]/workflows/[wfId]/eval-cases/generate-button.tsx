'use client'

import { useActionState } from 'react'
import { useFormStatus } from 'react-dom'
import { generateEvalCasesAction, type EvalCasesActionState } from './actions'

const initialState: EvalCasesActionState = { error: null, generated: null }

export function GenerateEvalCasesButton({
 wsId,
 wfId,
 hasExisting,
}: {
 wsId: string
 wfId: string
 hasExisting: boolean
}) {
 const action = generateEvalCasesAction.bind(null, wsId, wfId)
 const [state, formAction] = useActionState(action, initialState)

 return (
 <form action={formAction} style={{ display: 'inline-flex', flexDirection: 'column', gap: 8 }}>
 <SubmitButton hasExisting={hasExisting} />
 {state.error ? (
 <div role="alert" className="api-banner" style={{ marginTop: 8 }}>
 <strong>Generation failed.</strong> {state.error}
 </div>
 ) : null}
 {state.generated !== null && !state.error ? (
 <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
 Generated {state.generated} new case{state.generated === 1 ? '' : 's'}.
 </p>
 ) : null}
 </form>
 )
}

function SubmitButton({ hasExisting }: { hasExisting: boolean }) {
 const { pending } = useFormStatus return (
 <button
 type="submit"
 className="btn btn-primary"
 disabled={pending}
 aria-disabled={pending}
 >
 {pending ? (
 <>
 <span className="spinner" aria-hidden /> Generating eval cases…
 </>
 ) : hasExisting ? (
 <>+ Generate more eval cases</>
 ) : (
 <>Generate eval cases &rsaquo;</>
 )}
 </button>
 )
}
