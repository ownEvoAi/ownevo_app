// Next.js middleware: auth gating + workspace provisioning redirect.
//
// Uses the edge-safe auth config (no providers, no kernel calls) to parse the
// session cookie and check authentication state on every request.
//
// Three guards, in order:
//   1. Demo-invite cookie: if DEMO_MODE=true and ?invite=<token> is present,
//      strip it from the URL and set a HttpOnly cookie (existing behavior).
//   2. Auth gate: unauthenticated requests → /api/auth/signin (except
//      /api/auth/* itself, which Auth.js owns).
//   3. Workspace gate: authenticated users with no workspace membership →
//      /setup/new-workspace (the create-workspace screen). /setup/* itself is
//      allowed so the user can fill in the form.
import NextAuth from 'next-auth'
import { NextResponse } from 'next/server'
import { authConfig } from '@/auth.config'

const { auth } = NextAuth(authConfig)

const INVITE_PARAM = 'invite'
const INVITE_COOKIE = 'ownevo_demo_invite'
const ONE_YEAR_SECONDS = 365 * 24 * 60 * 60

export default auth(function middleware(req) {
 const { pathname } = req.nextUrl

 // ── 1. Demo invite cookie ─────────────────────────────────────────────────
 // When DEMO_MODE=true, strip the ?invite= token from the URL and store it
 // as a HttpOnly cookie. The kernel re-verifies the token on every
 // quota-gated request; the middleware does not need to know the signing key.
 if (process.env.DEMO_MODE?.toLowerCase() === 'true') {
  const inviteToken = req.nextUrl.searchParams.get(INVITE_PARAM)
  // Max 2048 chars matches the kernel's RedeemInviteRequest.token validation.
  // Silently drop oversized values rather than storing them as oversized cookies.
  if (inviteToken && inviteToken.length <= 2048) {
   const cleaned = req.nextUrl.clone()
   cleaned.searchParams.delete(INVITE_PARAM)
   const response = NextResponse.redirect(cleaned)
   response.cookies.set({
    name: INVITE_COOKIE,
    value: inviteToken,
    httpOnly: true,
    sameSite: 'lax',
    secure: req.nextUrl.protocol === 'https:',
    maxAge: ONE_YEAR_SECONDS,
    path: '/',
   })
   return response
  }
 }

 // ── 2. Auth gate ──────────────────────────────────────────────────────────
 // /api/auth/* is owned by Auth.js — never redirect it.
 if (pathname.startsWith('/api/auth')) {
  return NextResponse.next()
 }

 const session = req.auth
 if (!session) {
  const signInUrl = req.nextUrl.clone()
  signInUrl.pathname = '/api/auth/signin'
  signInUrl.searchParams.set('callbackUrl', req.url)
  return NextResponse.redirect(signInUrl)
 }

 // ── 3. Workspace gate ─────────────────────────────────────────────────────
 // /setup/* is the workspace creation screen; allow it even without a workspace.
 const hasWorkspace = Array.isArray(session.workspaces) && session.workspaces.length > 0
 if (!hasWorkspace && !pathname.startsWith('/setup/')) {
  const setupUrl = req.nextUrl.clone()
  setupUrl.pathname = '/setup/new-workspace'
  return NextResponse.redirect(setupUrl)
 }

 return NextResponse.next()
})

// Match every route except Next.js internals and static assets.
export const config = {
 matcher: ['/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml).*)'],
}
