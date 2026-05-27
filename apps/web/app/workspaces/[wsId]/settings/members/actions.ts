'use server'

import { revalidatePath } from 'next/cache'
import { auth } from '@/auth'
import { mintInvite, revokeInvite, type InviteRole } from '@/lib/kernel-invites'
import type { SyncedWorkspace } from '@/lib/kernel-sync'

export interface InviteFormState {
 error: string | null
 success: { inviteUrl: string; email: string } | null
}

const INITIAL_FORM_STATE: InviteFormState = { error: null, success: null }

function buildInviteUrl(token: string): string {
 // The accept page reads the token from the path segment. Build an absolute
 // URL when a public origin is configured (cookie/email previews), falling
 // back to a relative path the inviter can paste into a browser on the same
 // origin they're using right now.
 const origin = process.env.OWNEVO_WEB_ORIGIN ?? process.env.NEXTAUTH_URL
 const path = `/invites/${encodeURIComponent(token)}`
 if (!origin) return path
 return `${origin.replace(/\/$/, '')}${path}`
}

function adminRoleForWorkspace(
 workspaces: unknown,
 workspaceId: string,
): boolean {
 const list = Array.isArray(workspaces) ? (workspaces as SyncedWorkspace[]) : []
 const me = list.find((w) => w.id === workspaceId)
 return me?.role === 'owner' || me?.role === 'admin'
}

export async function inviteMemberAction(
 workspaceId: string,
 _prev: InviteFormState,
 formData: FormData,
): Promise<InviteFormState> {
 const session = await auth()
 if (!session?.user?.id) {
  return { ...INITIAL_FORM_STATE, error: 'Your session has expired. Sign in again.' }
 }
 if (!adminRoleForWorkspace(session.workspaces, workspaceId)) {
  return { ...INITIAL_FORM_STATE, error: 'You do not have permission to invite members to this workspace.' }
 }

 const email = String(formData.get('email') ?? '').trim().toLowerCase()
 const role = String(formData.get('role') ?? 'member') as InviteRole
 // Light-touch client-side validation; the kernel enforces the canonical rules.
 if (!email || !email.includes('@') || email.startsWith('@') || email.endsWith('@')) {
  return { ...INITIAL_FORM_STATE, error: 'Enter a valid email address.' }
 }
 if (role !== 'admin' && role !== 'member') {
  return { ...INITIAL_FORM_STATE, error: 'Role must be admin or member.' }
 }

 let minted
 try {
  minted = await mintInvite({
   workspaceId,
   inviterUserId: session.user.id,
   invitedEmail: email,
   role,
  })
 } catch (err) {
  console.error('[inviteMemberAction] mint failed:', err)
  return { ...INITIAL_FORM_STATE, error: 'Could not create invite. Please try again.' }
 }

 revalidatePath(`/workspaces/${workspaceId}/settings/members`)
 return {
  error: null,
  success: { inviteUrl: buildInviteUrl(minted.token), email },
 }
}

export interface RevokeInviteState {
 error: string | null
}

export async function revokeInviteAction(
 workspaceId: string,
 inviteId: string,
 _prev: RevokeInviteState,
): Promise<RevokeInviteState> {
 const session = await auth()
 if (!session?.user?.id) {
  return { error: 'Your session has expired. Sign in again.' }
 }
 if (!adminRoleForWorkspace(session.workspaces, workspaceId)) {
  return { error: 'You do not have permission to revoke invites for this workspace.' }
 }
 try {
  await revokeInvite(inviteId, session.user.id)
 } catch (err) {
  console.error('[revokeInviteAction] revoke failed:', err)
  return { error: 'Could not revoke invite. Please try again.' }
 }
 revalidatePath(`/workspaces/${workspaceId}/settings/members`)
 return { error: null }
}
