'use client'

import { useActionState } from 'react'
import { revokeInviteAction, type RevokeInviteState } from './actions'

const INITIAL: RevokeInviteState = { error: null }

export function RevokeInviteButton({
 workspaceId,
 inviteId,
 invitedEmail,
}: {
 workspaceId: string
 inviteId: string
 invitedEmail: string
}) {
 const boundAction = revokeInviteAction.bind(null, workspaceId, inviteId)
 const [state, dispatch, pending] = useActionState<RevokeInviteState, FormData>(
  boundAction,
  INITIAL,
 )
 return (
  <form
   action={dispatch}
   onSubmit={(e) => {
    if (!confirm(`Revoke the pending invite for ${invitedEmail}?`)) {
     e.preventDefault()
    }
   }}
   style={{ display: 'inline' }}
  >
   <button
    type="submit"
    disabled={pending}
    aria-label={`Revoke invite for ${invitedEmail}`}
    style={{
     padding: '4px 10px',
     fontSize: 12,
     border: '1px solid var(--border)',
     borderRadius: 4,
     background: 'transparent',
     color: 'var(--text)',
     cursor: pending ? 'not-allowed' : 'pointer',
     opacity: pending ? 0.55 : 1,
    }}
   >
    {pending ? 'Revoking…' : 'Revoke'}
   </button>
   {state.error ? (
    <span role="alert" className="settings-error" style={{ marginLeft: 8 }}>
     {state.error}
    </span>
   ) : null}
  </form>
 )
}
