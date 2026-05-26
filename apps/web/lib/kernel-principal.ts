// Server-only: derive the kernel auth header for the current request.
//
// Reads the Auth.js session, mints a short-lived signed workspace assertion
// from the resolved principal (internal user id + active workspace), and
// returns it as an Authorization: Bearer header for kernel calls.
//
// Returns no header (empty object) when there is no shared signing key or no
// resolvable principal — under OWNEVO_DEV_AUTH=true the kernel then resolves
// its seeded dev fallback, so zero-config local dev keeps working.
import 'server-only'
import { auth } from '@/auth'
import { mintWorkspaceAssertion } from './assertion'

// Short TTL: the header is minted fresh per request, so minutes is ample and
// bounds how long a leaked assertion stays valid.
const ASSERTION_TTL_SECONDS = 120

export async function kernelAuthHeaders(): Promise<Record<string, string>> {
  const key = process.env.OWNEVO_INTERNAL_AUTH_KEY
  if (!key) {
    return {}
  }
  let session
  try {
    session = await auth()
  } catch {
    // Called outside a request context (e.g. a build-time prerender). No
    // principal to assert; fall through to the unauthenticated path.
    return {}
  }
  const userId = session?.user?.id
  const workspaceId = session?.activeWorkspaceId
  if (!userId || !workspaceId) {
    return {}
  }
  const token = mintWorkspaceAssertion({
    userId,
    workspaceId,
    ttlSeconds: ASSERTION_TTL_SECONDS,
    signingKey: key,
  })
  return { authorization: `Bearer ${token}` }
}
