// Edge-safe Auth.js configuration used by Next.js middleware.
//
// Providers are intentionally absent: the middleware only parses the signed
// JWT session cookie to check authentication state — it never runs an OAuth
// flow. The full provider config (Credentials + Google) lives in auth.ts,
// which is imported by server components and route handlers only.
//
// The session callback is defined here (not only in auth.ts) because the
// middleware's req.auth is shaped by whichever NextAuth instance the
// middleware imports. Keeping the callback here means middleware sees the
// same workspaces / activeWorkspaceId fields as server components.
import type { NextAuthConfig } from 'next-auth'
import type { SyncedWorkspace } from '@/lib/kernel-sync'

export const authConfig = {
 providers: [],
 session: { strategy: 'jwt' },
 callbacks: {
  session({ session, token }) {
   // JWT fields are loosely typed (Record<string, unknown>); narrow on read.
   const userId = typeof token.userId === 'string' ? token.userId : null
   if (userId) {
    session.user.id = userId
    session.workspaces = (token.workspaces as SyncedWorkspace[] | undefined) ?? []
    session.activeWorkspaceId =
     typeof token.activeWorkspaceId === 'string' ? token.activeWorkspaceId : null
   }
   return session
  },
 },
} satisfies NextAuthConfig
