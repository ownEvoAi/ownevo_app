'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { deleteEvalCaseAction } from './lifecycle-actions'

// Per-row delete control. Two-step confirmation inline — first click
// arms, second click commits. Prevents accidental clicks dropping a
// case the operator spent time curating.
export function DeleteEvalCaseButton({
  wsId,
  wfId,
  caseId,
}: {
  wsId: string
  wfId: string
  caseId: string
}) {
  const router = useRouter()
  const [isPending, startTransition] = useTransition()
  const [armed, setArmed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleClick() {
    if (!armed) {
      setArmed(true)
      // Auto-disarm after 4s so a stray click on row 1 doesn't linger
      // when the operator moves to row 5.
      setTimeout(() => setArmed(false), 4000)
      return
    }
    setError(null)
    startTransition(async () => {
      const result = await deleteEvalCaseAction({ wsId, wfId, caseId })
      if (!result.ok) {
        setError(result.error)
        setArmed(false)
        return
      }
      router.refresh()
    })
  }

  return (
    <div className="eval-delete-cell">
      <button
        type="button"
        onClick={handleClick}
        disabled={isPending}
        className={armed ? 'btn btn-danger' : 'btn btn-secondary'}
        style={{ fontSize: 11, padding: '4px 10px' }}
        title={armed ? 'Click again to confirm' : 'Remove this eval case'}
      >
        {isPending ? '…' : armed ? 'Confirm?' : 'Remove'}
      </button>
      {error ? <span className="eval-delete-error">{error}</span> : null}
    </div>
  )
}
