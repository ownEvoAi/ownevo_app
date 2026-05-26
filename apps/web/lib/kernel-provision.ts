// Server-only: workspace provisioning via the kernel's internal endpoint.
//
// Called from server actions after authentication is established. The shared
// service token (OWNEVO_INTERNAL_AUTH_KEY) authenticates this call — the same
// key used by the auth-sync endpoint.
import 'server-only'

const INTERNAL_AUTH_KEY = process.env.OWNEVO_INTERNAL_AUTH_KEY
const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

export interface ProvisionedWorkspace {
 workspace_id: string
 name: string
}

/**
 * Ask the kernel to create a new workspace and make `userId` its owner.
 *
 * The caller is responsible for updating the session (via unstable_update)
 * after this resolves so the new workspace appears in the JWT immediately.
 *
 * Throws if the shared auth key is missing or the kernel returns an error.
 */
export async function createWorkspace(
 userId: string,
 name: string,
): Promise<ProvisionedWorkspace> {
 if (!INTERNAL_AUTH_KEY) {
  throw new Error(
   'OWNEVO_INTERNAL_AUTH_KEY is required to provision workspaces',
  )
 }

 const res = await fetch(`${API_URL}/api/internal/workspaces`, {
  method: 'POST',
  headers: {
   'content-type': 'application/json',
   authorization: `Bearer ${INTERNAL_AUTH_KEY}`,
  },
  body: JSON.stringify({ user_id: userId, name }),
  signal: AbortSignal.timeout(10_000),
 })

 if (!res.ok) {
  const detail = await res.text().catch(() => res.statusText)
  throw new Error(`workspace create failed (${res.status}): ${detail}`)
 }

 const data = (await res.json()) as ProvisionedWorkspace
 if (typeof data?.workspace_id !== 'string' || !data.workspace_id) {
  throw new Error('kernel returned an invalid workspace payload')
 }
 return data
}
