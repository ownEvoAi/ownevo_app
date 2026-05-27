'use client'

import { useActionState, useRef, useState } from 'react'
import { inviteMemberAction, type InviteFormState } from './actions'

const INITIAL: InviteFormState = { error: null, success: null }

export function InviteMemberForm({ workspaceId }: { workspaceId: string }) {
 const boundAction = inviteMemberAction.bind(null, workspaceId)
 const [state, dispatch, pending] = useActionState<InviteFormState, FormData>(
  boundAction,
  INITIAL,
 )
 const urlRef = useRef<HTMLInputElement | null>(null)
 const [copied, setCopied] = useState(false)

 async function copyToClipboard() {
  if (!state.success) return
  try {
   await navigator.clipboard.writeText(state.success.inviteUrl)
   setCopied(true)
   setTimeout(() => setCopied(false), 1500)
  } catch {
   urlRef.current?.select()
  }
 }

 return (
  <div className="settings-card">
   <div className="settings-card-header">
    <h2 className="settings-card-title">Invite a new member</h2>
    <p className="settings-card-subtitle">
     Generates a link the invitee can use to join. The link is single-use,
     bound to the recipient&apos;s email, and expires in seven days. You
     send the link through your own channel — ownEvo does not email it.
    </p>
   </div>
   <form action={dispatch} className="setup-form">
    <div className="setup-field">
     <label className="setup-label" htmlFor="invite-email">
      Email address
     </label>
     <input
      id="invite-email"
      name="email"
      type="email"
      autoComplete="off"
      required
      placeholder="teammate@company.com"
      className="setup-input"
     />
    </div>
    <div className="setup-field">
     <label className="setup-label" htmlFor="invite-role">
      Role
     </label>
     <select
      id="invite-role"
      name="role"
      defaultValue="member"
      className="setup-input"
     >
      <option value="member">Member — can use the workspace</option>
      <option value="admin">Admin — can invite and revoke members</option>
     </select>
    </div>
    {state.error ? (
     <p role="alert" className="setup-error">
      {state.error}
     </p>
    ) : null}
    <button type="submit" disabled={pending} className="setup-submit">
     {pending ? 'Creating invite…' : 'Create invite link'}
    </button>
   </form>
   {state.success ? (
    <div
     role="status"
     style={{
      marginTop: 16,
      padding: 12,
      borderRadius: 6,
      background: 'var(--surface-2, rgba(59, 130, 246, 0.08))',
      border: '1px solid var(--border)',
     }}
    >
     <p style={{ margin: 0, marginBottom: 8, fontSize: 13 }}>
      Invite for <strong>{state.success.email}</strong> is ready. Share
      this link with them — it will not be shown again.
     </p>
     <div style={{ display: 'flex', gap: 8 }}>
      <input
       ref={urlRef}
       readOnly
       value={state.success.inviteUrl}
       onFocus={(e) => e.currentTarget.select()}
       className="setup-input"
       style={{ flex: 1 }}
      />
      <button
       type="button"
       onClick={copyToClipboard}
       className="setup-submit"
       style={{ width: 'auto', padding: '0 14px' }}
      >
       {copied ? 'Copied' : 'Copy'}
      </button>
     </div>
    </div>
   ) : null}
  </div>
 )
}
