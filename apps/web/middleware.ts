import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

// Demo-only middleware: when a visitor lands with `?invite=<token>` set
// the `ownevo_demo_invite` cookie HttpOnly and redirect to the clean
// URL. The kernel re-verifies the signature + revocation status on
// every quota-gated request, so the middleware does not need to know
// the signing key.
//
// Outside demo mode the middleware is a no-op — every request returns
// the unmodified NextResponse.next(). This keeps local dev and
// production deploys unaffected by demo plumbing.

const INVITE_PARAM = 'invite'
const INVITE_COOKIE = 'ownevo_demo_invite'
const ONE_YEAR_SECONDS = 365 * 24 * 60 * 60

export function middleware(req: NextRequest) {
  if (process.env.DEMO_MODE?.toLowerCase() !== 'true') {
    return NextResponse.next()
  }
  const inviteToken = req.nextUrl.searchParams.get(INVITE_PARAM)
  // Max 2048 chars matches the kernel's RedeemInviteRequest.token validation.
  // Silently drop oversized values rather than storing them as oversized cookies.
  if (!inviteToken || inviteToken.length > 2048) {
    return NextResponse.next()
  }

  // Build the clean URL (preserve everything except `invite`) and
  // attach the cookie to the redirect. HttpOnly so a script on the
  // demo origin cannot read it; the kernel re-validates on every
  // request that needs the elevated tier.
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

// Match every route except Next internals and static assets — the
// `?invite=` redirect needs to land on any landing page.
export const config = {
  matcher: ['/((?!_next/|favicon.ico|robots.txt|sitemap.xml).*)'],
}
