// Server-only client for the kernel's internal auth-sync endpoint.
//
// Called from the Auth.js `jwt` callback right after a user authenticates, to
// upsert the user + provider identity in the kernel and read back the
// workspaces they belong to. Authenticated with the shared
// OWNEVO_INTERNAL_AUTH_KEY presented as a bearer token (the service secret,
// not a workspace assertion — no workspace is bound at sync time).
import 'server-only'

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

export interface SyncedWorkspace {
  id: string
  name: string
  role: string
}

export interface SyncedPrincipal {
  user_id: string
  email: string
  workspaces: SyncedWorkspace[]
}

export async function syncPrincipal(input: {
  provider: string
  providerSub: string
  email: string
  displayName?: string | null
}): Promise<SyncedPrincipal> {
  const key = process.env.OWNEVO_INTERNAL_AUTH_KEY
  if (!key) {
    throw new Error(
      'OWNEVO_INTERNAL_AUTH_KEY is not set; cannot sync the principal with the kernel',
    )
  }
  const res = await fetch(`${API_URL}/api/internal/auth/sync`, {
    method: 'POST',
    cache: 'no-store',
    // Bound sign-in latency: if the kernel is down, a missing timeout causes
    // every new sign-in to hang until the OS connection timeout fires (minutes).
    // 10 s is generous for a loopback/private-network call and keeps the Auth.js
    // jwt callback from blocking indefinitely.
    signal: AbortSignal.timeout(10_000),
    headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
    body: JSON.stringify({
      provider: input.provider,
      provider_sub: input.providerSub,
      email: input.email,
      display_name: input.displayName ?? null,
    }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(`kernel auth-sync failed (${res.status}): ${detail}`)
  }
  return (await res.json()) as SyncedPrincipal
}
