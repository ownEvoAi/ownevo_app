// Auth.js (NextAuth v5) configuration for the web edge.
//
// Authentication lives here, in the Next.js app, not in the Python kernel
// (see docs/AUTH.md). Two providers, each registered only when its
// prerequisites are present, so the same code serves local dev and cloud:
//
//   * dev (credentials) — registered when OWNEVO_DEV_AUTH=true. Logs in as a
//     seeded user with no external round-trip, so `make web-dev` and tests
//     need no Google credentials and no browser OAuth.
//   * Google (OIDC) — registered only when AUTH_GOOGLE_ID/SECRET are set, i.e.
//     a real deployment.
//
// The session is an encrypted JWT cookie (no server-side session table). On
// first sign-in the `jwt` callback syncs the principal with the kernel and
// stashes the internal user id + workspace memberships in the token; kernel
// calls then carry a short-lived signed assertion minted from that principal.
//
// unstable_update is exported for server actions that need to update the
// session in place (workspace create, workspace switch) without forcing a
// re-sign-in. It triggers the jwt callback with trigger='update'; the handler
// below merges the supplied fields into the existing token.
//
// Known limitation: externally-triggered membership changes (an admin removes
// a user from a workspace via a different session) are not reflected until the
// affected user re-signs-in. This is inherent to the encrypted-JWT strategy;
// a server-side session store would fix it if revocation ever becomes a hard
// requirement.
import NextAuth from 'next-auth'
import Credentials from 'next-auth/providers/credentials'
import Google from 'next-auth/providers/google'
import type { Provider } from 'next-auth/providers'
import { syncPrincipal, type SyncedWorkspace } from '@/lib/kernel-sync'
import { authConfig } from '@/auth.config'

// Mirror the kernel's seeded dev fallback (migration 0035): dev-user is owner
// of the 'default' workspace. Used only for zero-config local dev when no
// shared signing key is configured.
const DEV_USER_ID = 'dev-user'
const DEFAULT_WORKSPACE_ID = 'default'

function devAuthEnabled(): boolean {
 return process.env.OWNEVO_DEV_AUTH?.toLowerCase() === 'true'
}

function buildProviders(): Provider[] {
 const providers: Provider[] = []
 // The dev credentials provider accepts any email with no password. It must
 // never run alongside OWNEVO_INTERNAL_AUTH_KEY: the kernel's email-based
 // account-linking would let an attacker claim any existing user's workspaces
 // by submitting their email. Guard here so a staging env with both flags set
 // fails safely rather than silently opening an account-takeover path.
 if (devAuthEnabled() && !process.env.OWNEVO_INTERNAL_AUTH_KEY) {
  providers.push(
   Credentials({
    id: 'dev',
    name: 'Dev login',
    credentials: { email: { label: 'Email', type: 'email' } },
    authorize(credentials) {
     const email =
      typeof credentials?.email === 'string' && credentials.email.trim()
       ? credentials.email.trim()
       : 'dev@ownevo.local'
     // Default login maps to the seeded dev identity (provider_sub
     // 'dev-user'); a custom email keys its own identity by that email.
     const id = email === 'dev@ownevo.local' ? DEV_USER_ID : email
     return { id, email, name: 'Dev login' }
    },
   }),
  )
 }
 if (process.env.AUTH_GOOGLE_ID && process.env.AUTH_GOOGLE_SECRET) {
  providers.push(
   Google({
    clientId: process.env.AUTH_GOOGLE_ID,
    clientSecret: process.env.AUTH_GOOGLE_SECRET,
   }),
  )
 }
 return providers
}

export const { handlers, auth, signIn, signOut, unstable_update } = NextAuth({
 ...authConfig,
 providers: buildProviders(),
 callbacks: {
  // Inherit the session callback from authConfig so auth.ts and middleware
  // produce identical Session shapes from the same JWT.
  session: authConfig.callbacks!.session!,

  async signIn({ account, profile }) {
   // Google: require email_verified before allowing account linking.
   // An attacker can create an unverified Google OAuth account using someone
   // else's email address. Without this guard the kernel's email-match path
   // would link their identity to the victim's existing user row.
   if (account?.provider === 'google') {
    // The raw Google profile always includes email_verified; NextAuth types
    // it as unknown via the Profile index signature.
    const emailVerified = (profile as Record<string, unknown>)?.email_verified
    if (emailVerified !== true) {
     return false
    }
   }
   return true
  },

  async jwt({ token, user, account, trigger, session }) {
   // Handle session updates triggered by unstable_update (workspace create /
   // workspace switch). The caller supplies a partial session object; merge
   // only the fields it provides so unrelated token fields are preserved.
   if (trigger === 'update' && session != null) {
    const s = session as { activeWorkspaceId?: string | null; workspaces?: SyncedWorkspace[] }
    if (s.workspaces !== undefined) {
     // Validate each workspace entry so a buggy or future server action
     // cannot write an unrecognised role into the JWT.
     const VALID_ROLES = new Set<string>(['owner', 'admin', 'member'])
     token.workspaces = s.workspaces.filter(
      (w) => typeof w?.id === 'string' && w.id && VALID_ROLES.has(String(w.role)),
     )
    }
    if (typeof s.activeWorkspaceId !== 'undefined') {
     token.activeWorkspaceId = s.activeWorkspaceId
    }
    return token
   }

   // Only runs the sync on initial sign-in, when `account` + `user` are set.
   if (!account || !user) {
    return token
   }
   const provider = account.provider
   const providerSub = account.providerAccountId ?? String(user.id ?? '')
   const email = user.email ?? ''
   if (process.env.OWNEVO_INTERNAL_AUTH_KEY) {
    const synced = await syncPrincipal({
     provider,
     providerSub,
     email,
     displayName: user.name ?? null,
    })
    token.userId = synced.user_id
    token.workspaces = synced.workspaces
    token.activeWorkspaceId = synced.workspaces[0]?.id ?? null
   } else if (devAuthEnabled()) {
    // Zero-config local dev: no shared key, so mirror the kernel's seeded
    // dev fallback rather than calling the (key-gated) sync endpoint.
    token.userId = DEV_USER_ID
    token.workspaces = [
     { id: DEFAULT_WORKSPACE_ID, name: 'Default workspace', role: 'owner' },
    ]
    token.activeWorkspaceId = DEFAULT_WORKSPACE_ID
   } else {
    throw new Error(
     'OWNEVO_INTERNAL_AUTH_KEY is required to resolve the principal in production',
    )
   }
   return token
  },
 },
})
