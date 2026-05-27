'use client'

import Link from 'next/link'
import { useActionState } from 'react'
import { acceptInviteAction, type AcceptInviteState } from './actions'

const INITIAL: AcceptInviteState = { error: null, errorCode: null }

export function AcceptInviteForm({ token }: { token: string }) {
 // `bind` pins the token so the server action only sees state + formData.
 // The form has no inputs; the click alone is the user's confirmation.
 const boundAction = acceptInviteAction.bind(null, token)
 const [state, dispatch, pending] = useActionState<AcceptInviteState, FormData>(
  boundAction,
  INITIAL,
 )

 return (
  <form action={dispatch} className="setup-form">
   {state.error ? (
    <p role="alert" className="setup-error" style={{ marginBottom: 12 }}>
     {state.error}
    </p>
   ) : null}
   <button type="submit" disabled={pending} className="setup-submit">
    {pending ? 'Accepting…' : 'Accept invite'}
   </button>
   <Link
    href="/"
    className="setup-secondary"
    style={{ marginTop: 12, textAlign: 'center', display: 'block' }}
   >
    Not now
   </Link>
  </form>
 )
}
