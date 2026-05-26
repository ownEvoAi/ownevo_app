'use client'
import { useActionState } from 'react'
import { signInWithDev, type SignInResult } from './actions'

export function DevSignInForm({ callbackUrl }: { callbackUrl: string }) {
  const [state, dispatch, pending] = useActionState<SignInResult | null, FormData>(
    signInWithDev,
    null,
  )

  return (
    <form action={dispatch} className="setup-form">
      <input type="hidden" name="callbackUrl" value={callbackUrl} />
      <div className="setup-field">
        <label htmlFor="email" className="setup-label">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          placeholder="dev@ownevo.local"
          className="setup-input"
          autoComplete="email"
          autoFocus
        />
      </div>
      {state?.error && <p className="setup-error">{state.error}</p>}
      <button type="submit" disabled={pending} className="setup-submit">
        {pending ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  )
}
