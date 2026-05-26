'use server'

import { redirect } from 'next/navigation'
import { auth, unstable_update } from '@/auth'

/**
 * Switch the active workspace for the current session.
 *
 * Validates that the target workspace is in the user's current memberships
 * (read from the session JWT), then updates activeWorkspaceId in place via
 * unstable_update and redirects to the new workspace root.
 *
 * Note: this updates the self-reported session only. Externally-triggered
 * membership changes (an admin removes the user from a workspace) are not
 * reflected until re-sign-in — inherent to the JWT session strategy.
 */
export async function switchWorkspaceAction(formData: FormData) {
 const workspaceId = String(formData.get('workspaceId') ?? '').trim()
 if (!workspaceId) return

 const session = await auth()
 if (!session) return

 // Verify the target workspace is in the user's current memberships.
 const workspaces = Array.isArray(session.workspaces) ? session.workspaces : []
 const isMember = workspaces.some((w) => w.id === workspaceId)
 if (!isMember) {
  // The user does not belong to this workspace (or the token is stale).
  // Silently ignore rather than returning an error — the switcher only
  // shows workspaces from the session, so this path is only reachable if
  // the membership was revoked externally after the JWT was minted.
  return
 }

 await unstable_update({ activeWorkspaceId: workspaceId })
 redirect(`/workspaces/${workspaceId}`)
}
