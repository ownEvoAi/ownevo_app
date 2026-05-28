// Server-only client for the kernel's internal workspace-invite endpoints.
//
// All three calls present OWNEVO_INTERNAL_AUTH_KEY as a bearer token — the
// same shared service secret used by auth-sync and workspace provisioning.
// Authorization (admin check on mint/revoke; "this user is redeeming"
// attestation on redeem) is layered on top by passing the acting user id in
// the request body. The web edge has already authenticated the user via
// Auth.js by the time it calls these.
import 'server-only'

const INTERNAL_AUTH_KEY = process.env.OWNEVO_INTERNAL_AUTH_KEY
const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

export type InviteRole = 'admin' | 'member'

export interface MintInviteInput {
 workspaceId: string
 inviterUserId: string
 invitedEmail: string
 role: InviteRole
 ttlDays?: number
}

export interface MintedInvite {
 invite_id: string
 token: string
 expires_at: string
}

export interface RedeemedInvite {
 workspace_id: string
 workspace_name: string
 role: string
}

// The kernel returns structured error bodies on the redeem path so the web
// edge can render specific copy ("this invite has expired", "this invite
// has been revoked", etc.). Unwrap on miss and fall back to text.
export class InviteRedeemError extends Error {
 constructor(
  public code: string,
  message: string,
  public status: number,
 ) {
  super(message)
  this.name = 'InviteRedeemError'
 }
}

function requireKey(): string {
 if (!INTERNAL_AUTH_KEY) {
  throw new Error('OWNEVO_INTERNAL_AUTH_KEY is required to call the invite endpoints')
 }
 return INTERNAL_AUTH_KEY
}

export async function mintInvite(input: MintInviteInput): Promise<MintedInvite> {
 const key = requireKey()
 const res = await fetch(
  `${API_URL}/api/internal/workspaces/${encodeURIComponent(input.workspaceId)}/invites`,
  {
   method: 'POST',
   headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
   body: JSON.stringify({
    inviter_user_id: input.inviterUserId,
    invited_email: input.invitedEmail,
    role: input.role,
    ttl_days: input.ttlDays,
   }),
   signal: AbortSignal.timeout(10_000),
  },
 )
 if (!res.ok) {
  const detail = await res.text().catch(() => res.statusText)
  throw new Error(`mint invite failed (${res.status}): ${detail}`)
 }
 return (await res.json()) as MintedInvite
}

export async function redeemInvite(token: string, redeemerUserId: string): Promise<RedeemedInvite> {
 const key = requireKey()
 const res = await fetch(`${API_URL}/api/internal/invites/redeem`, {
  method: 'POST',
  headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
  body: JSON.stringify({ token, redeemer_user_id: redeemerUserId }),
  signal: AbortSignal.timeout(10_000),
 })
 if (!res.ok) {
  // Kernel emits {detail: {code, message}} for invite errors and a plain
  // string detail otherwise. Sniff for the structured form so the UI can
  // branch on `code` without parsing the human message.
  let code = 'invite_error'
  let message = res.statusText
  try {
   const body = (await res.json()) as { detail?: unknown }
   if (typeof body.detail === 'object' && body.detail !== null) {
    const d = body.detail as { code?: unknown; message?: unknown }
    if (typeof d.code === 'string') code = d.code
    if (typeof d.message === 'string') message = d.message
   } else if (typeof body.detail === 'string') {
    message = body.detail
   }
  } catch {
   // Body wasn't JSON; fall through with defaults.
  }
  throw new InviteRedeemError(code, message, res.status)
 }
 return (await res.json()) as RedeemedInvite
}

export async function revokeInvite(inviteId: string, actorUserId: string): Promise<void> {
 const key = requireKey()
 const res = await fetch(
  `${API_URL}/api/internal/invites/${encodeURIComponent(inviteId)}/revoke`,
  {
   method: 'POST',
   headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
   body: JSON.stringify({ actor_user_id: actorUserId }),
   signal: AbortSignal.timeout(10_000),
  },
 )
 if (!res.ok) {
  const detail = await res.text().catch(() => res.statusText)
  throw new Error(`revoke invite failed (${res.status}): ${detail}`)
 }
}

export interface WorkspaceMember {
 user_id: string
 email: string
 display_name: string | null
 role: string
 joined_at: string
}

export interface PendingInvite {
 invite_id: string
 invited_email: string
 role: string
 invited_by_user_id: string
 invited_by_email: string | null
 invited_by_display_name: string | null
 created_at: string
 expires_at: string
}

export async function listWorkspaceMembers(
 workspaceId: string,
 actorUserId: string,
): Promise<WorkspaceMember[]> {
 const key = requireKey()
 const url = new URL(
  `${API_URL}/api/internal/workspaces/${encodeURIComponent(workspaceId)}/members`,
 )
 url.searchParams.set('actor_user_id', actorUserId)
 const res = await fetch(url, {
  method: 'GET',
  headers: { authorization: `Bearer ${key}` },
  signal: AbortSignal.timeout(10_000),
  cache: 'no-store',
 })
 if (!res.ok) {
  const detail = await res.text().catch(() => res.statusText)
  throw new Error(`list members failed (${res.status}): ${detail}`)
 }
 const body = (await res.json()) as { members: WorkspaceMember[] }
 return body.members
}

// Status values the preview endpoint returns. Keep this in lockstep with the
// kernel constants in routes/internal_invites.py; the accept page branches on
// the exact strings.
export type InvitePreviewStatus =
 | 'pending'
 | 'expired'
 | 'revoked'
 | 'redeemed_by_me'
 | 'redeemed_by_other'
 | 'email_mismatch'
 | 'workspace_gone'

export interface InvitePreview {
 status: InvitePreviewStatus
 workspace_id: string
 workspace_name: string | null
 invited_email: string
 role: string
 invited_by_email: string | null
 invited_by_display_name: string | null
 expires_at: string
}

export async function previewInvite(
 token: string,
 actorUserId: string,
): Promise<InvitePreview> {
 const key = requireKey()
 const url = new URL(`${API_URL}/api/internal/invites/preview`)
 url.searchParams.set('token', token)
 url.searchParams.set('actor_user_id', actorUserId)
 const res = await fetch(url, {
  method: 'GET',
  headers: { authorization: `Bearer ${key}` },
  signal: AbortSignal.timeout(10_000),
  cache: 'no-store',
 })
 if (!res.ok) {
  // The kernel emits {detail: {code, message}} for invite-shape errors
  // (signature / not-found), matching the redeem endpoint. Reuse the same
  // typed error so callers can branch on `code` without re-parsing.
  let code = 'invite_error'
  let message = res.statusText
  try {
   const body = (await res.json()) as { detail?: unknown }
   if (typeof body.detail === 'object' && body.detail !== null) {
    const d = body.detail as { code?: unknown; message?: unknown }
    if (typeof d.code === 'string') code = d.code
    if (typeof d.message === 'string') message = d.message
   } else if (typeof body.detail === 'string') {
    message = body.detail
   }
  } catch {
   // Body wasn't JSON; fall through with defaults.
  }
  throw new InviteRedeemError(code, message, res.status)
 }
 return (await res.json()) as InvitePreview
}

export async function listPendingInvites(
 workspaceId: string,
 actorUserId: string,
): Promise<PendingInvite[]> {
 const key = requireKey()
 const url = new URL(
  `${API_URL}/api/internal/workspaces/${encodeURIComponent(workspaceId)}/invites`,
 )
 url.searchParams.set('actor_user_id', actorUserId)
 const res = await fetch(url, {
  method: 'GET',
  headers: { authorization: `Bearer ${key}` },
  signal: AbortSignal.timeout(10_000),
  cache: 'no-store',
 })
 if (!res.ok) {
  const detail = await res.text().catch(() => res.statusText)
  throw new Error(`list invites failed (${res.status}): ${detail}`)
 }
 const body = (await res.json()) as { invites: PendingInvite[] }
 return body.invites
}
