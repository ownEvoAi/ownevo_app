'use client'

import { useActionState } from 'react'
import { createWorkspaceAction, type ActionResult } from './actions'

const INITIAL_STATE: ActionResult = {}

export function WorkspaceCreateForm() {
 const [state, dispatch, isPending] = useActionState(createWorkspaceAction, INITIAL_STATE)

 return (
  <form action={dispatch} className="setup-form">
   <div className="setup-field">
    <label htmlFor="name" className="setup-label">
     Workspace name
    </label>
    <input
     id="name"
     name="name"
     type="text"
     required
     maxLength={80}
     autoFocus
     placeholder="e.g. Acme Demand Planning"
     className="setup-input"
     aria-describedby={state.error ? 'name-error' : undefined}
    />
    {state.error && (
     <p id="name-error" className="setup-error" role="alert">
      {state.error}
     </p>
    )}
   </div>

   <button type="submit" disabled={isPending} className="setup-submit">
    {isPending ? 'Creating…' : 'Create workspace'}
   </button>
  </form>
 )
}
