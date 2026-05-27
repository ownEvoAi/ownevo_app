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
