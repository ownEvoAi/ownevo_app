'use server'

import { redirect } from 'next/navigation'
import { auth, unstable_update } from '@/auth'
import { createWorkspace } from '@/lib/kernel-provision'
import type { SyncedWorkspace } from '@/lib/kernel-sync'

export interface ActionResult {
 error?: string
}

/**
 * Create a new workspace for the authenticated user.
 *
 * On success: updates the session JWT in place (unstable_update) so the new
 * workspace appears in the sidebar immediately, then redirects the browser to
 * the workspace root. The kernel call and the JWT update are separate
 * operations — if the redirect is followed successfully the workspace exists
 * in both the DB and the session.
 */
export async function createWorkspaceAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
 const session = await auth()
 if (!session?.user?.id) {
  return { error: 'Not authenticated' }
 }

 const rawName = String(formData.get('name') ?? '').trim()
 if (!rawName) {
  return { error: 'Workspace name is required' }
 }
 if (rawName.length > 80) {
  return { error: 'Workspace name must be 80 characters or fewer' }
 }

 let provisioned: { workspace_id: string; name: string }
 try {
  provisioned = await createWorkspace(session.user.id, rawName)
 } catch (err) {
  const msg = err instanceof Error ? err.message : String(err)
  return { error: `Could not create workspace: ${msg}` }
 }

 // Merge the new workspace into the existing session memberships and activate it.
 const existing: SyncedWorkspace[] = Array.isArray(session.workspaces) ? session.workspaces : []
 const newWorkspace: SyncedWorkspace = {
  id: provisioned.workspace_id,
  name: provisioned.name,
  role: 'owner',
 }
 await unstable_update({
  workspaces: [...existing, newWorkspace],
  activeWorkspaceId: provisioned.workspace_id,
 })

 redirect(`/workspaces/${provisioned.workspace_id}`)
}
