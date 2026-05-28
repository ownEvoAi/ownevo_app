'use server'

import { redirect } from 'next/navigation'
import { auth, signOut, unstable_update } from '@/auth'
import { InviteRedeemError, redeemInvite } from '@/lib/kernel-invites'
import type { SyncedWorkspace } from '@/lib/kernel-sync'

export interface AcceptInviteState {
 error: string | null
 errorCode: string | null
}

// Map kernel error codes to human-readable copy. Keep the messages short
// and actionable; the page renders them verbatim with no further parsing.
const ERROR_COPY: Record<string, string> = {
 invite_invalid:
  'This invite link is not valid. Ask the person who sent it for a new link.',
 invite_expired:
  'This invite has expired. Ask the person who invited you for a fresh link.',
 invite_revoked:
  'This invite has been revoked. Ask the person who invited you for a new one.',
 invite_already_redeemed:
  'This invite has already been used by another account.',
 invite_email_mismatch:
  'This invite was sent to a different email address. Sign in with the correct account to accept it.',
}

export async function signOutToReclaimInvite(callbackUrl: string): Promise<void> {
 await signOut({ redirectTo: callbackUrl })
}

export async function acceptInviteAction(
 token: string,
 _prev: AcceptInviteState,
): Promise<AcceptInviteState> {
 const session = await auth()
 if (!session?.user?.id) {
  // The accept page already gates on this; this branch covers a session
  // that expired between page load and form submit.
  return {
   error: 'Your session has expired. Sign in again to accept this invite.',
   errorCode: 'session_expired',
  }
 }

 let redeemed
 try {
  redeemed = await redeemInvite(token, session.user.id)
 } catch (err) {
  if (err instanceof InviteRedeemError) {
   return {
    error: ERROR_COPY[err.code] ?? err.message,
    errorCode: err.code,
   }
  }
  console.error('[acceptInviteAction] redeem failed:', err)
  return {
   error: 'Could not accept this invite. Please try again.',
   errorCode: 'unknown',
  }
 }

 // Merge the new workspace into the session memberships. The redemption is
 // idempotent on the kernel side, so a duplicate entry here is also possible
 // if the user already had this workspace (e.g. they redeemed the same invite
 // twice from two tabs). Dedupe by id.
 const existing: SyncedWorkspace[] = Array.isArray(session.workspaces)
  ? session.workspaces
  : []
 const dedup = existing.filter((w) => w.id !== redeemed.workspace_id)
 const next: SyncedWorkspace[] = [
  ...dedup,
  { id: redeemed.workspace_id, name: redeemed.workspace_name, role: redeemed.role },
 ]

 try {
  await unstable_update({
   workspaces: next,
   activeWorkspaceId: redeemed.workspace_id,
  })
 } catch (err) {
  // JWT update failed. Redirect through sign-in to force a fresh JWT
  // before navigating to the workspace — otherwise the middleware workspace
  // gate sees the stale session (no workspaces) and kicks the user to
  // /setup/new-workspace instead.
  console.error('[acceptInviteAction] unstable_update failed:', err)
  redirect(
   `/auth/signin?callbackUrl=${encodeURIComponent(`/workspaces/${redeemed.workspace_id}`)}`,
  )
 }

 redirect(`/workspaces/${redeemed.workspace_id}`)
}
