// Web→kernel identity assertion minter (server-only).
//
// Mirrors the Python `mint_workspace_assertion` in
// apps/kernel/src/ownevo_kernel/api/_internal_auth.py. The kernel verifies an
// assertion by recomputing HMAC-SHA256 over the received base64url payload
// string and then JSON-parsing that payload, so the two sides do not need
// byte-identical serialization — only a self-consistent (payload, signature)
// pair carrying { u, w, e }. We still emit the same sorted-compact JSON shape
// Python produces so a token minted on either side is interchangeable.
//
// Format: base64url(payload) + "." + base64url(hmac_sha256(base64url(payload))).
// The HMAC covers the base64url payload *string*, matching the kernel's
// `_signing.sign(payload_s, key)`.
import 'server-only'
import { createHmac } from 'node:crypto'

export interface MintAssertionOptions {
  userId: string
  workspaceId: string
  ttlSeconds: number
  signingKey: string
  // Override the issued-at epoch (seconds) for deterministic tests.
  issuedAt?: number
}

export function mintWorkspaceAssertion(opts: MintAssertionOptions): string {
  const { userId, workspaceId, ttlSeconds, signingKey, issuedAt } = opts
  if (!userId || !workspaceId) {
    throw new Error('userId and workspaceId are required')
  }
  if (ttlSeconds <= 0) {
    throw new Error('ttlSeconds must be positive')
  }
  const iat = issuedAt ?? Math.floor(Date.now() / 1000)
  // Key order { e, u, w } + JSON.stringify's default comma/colon separators
  // reproduce Python's json.dumps(sort_keys=True, separators=(',', ':')) for
  // the ASCII ids the system mints.
  const payloadJson = JSON.stringify({ e: iat + ttlSeconds, u: userId, w: workspaceId })
  const payloadB64 = Buffer.from(payloadJson, 'utf8').toString('base64url')
  const sigB64 = createHmac('sha256', signingKey).update(payloadB64).digest('base64url')
  return `${payloadB64}.${sigB64}`
}
