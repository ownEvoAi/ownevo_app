'use server'

import { redirect } from 'next/navigation'
import { auth, unstable_update } from '@/auth'
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
  // The kernel state is correct; the JWT failed to update in place. The
  // next sign-in will re-sync. Continue to the redirect so the user lands
  // somewhere reasonable.
  console.error('[acceptInviteAction] unstable_update failed:', err)
 }

 redirect(`/workspaces/${redeemed.workspace_id}`)
}
