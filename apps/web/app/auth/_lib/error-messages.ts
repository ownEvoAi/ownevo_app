// Canonical Auth.js error code → message mapping.
//
// AUTH_ERROR_PAGES: full heading + body for the standalone /auth/error page.
// AUTH_ERROR_INLINE: single-line string for inline banners on the sign-in page.
//
// Both maps share the same error code keys so they stay in sync. Add new
// codes to both maps, or to only one if the code can only appear in that
// surface (e.g. CredentialsSignin is sign-in-only).

export const AUTH_ERROR_PAGES: Record<string, { heading: string; body: string }> = {
  AccessDenied: {
    heading: 'Access denied',
    body: "Your Google account's email address is not verified. Please verify your email with Google and try again.",
  },
  OAuthSignin: {
    heading: 'Sign-in error',
    body: 'There was a problem starting the sign-in flow. Please try again.',
  },
  OAuthCallback: {
    heading: 'Sign-in error',
    body: 'There was a problem completing the Google sign-in. This can happen if the browser tab was closed mid-flow. Please try again.',
  },
  OAuthAccountNotLinked: {
    heading: 'Account conflict',
    body: 'An account with this email already exists but was created with a different sign-in method. Please sign in with the original method.',
  },
  Configuration: {
    heading: 'Sign-in temporarily unavailable',
    body: 'The authentication service could not be reached. Please try again — if the problem persists, contact the workspace administrator.',
  },
  Verification: {
    heading: 'Link expired',
    body: 'The sign-in link has expired or has already been used. Please request a new one.',
  },
}

export const DEFAULT_AUTH_ERROR_PAGE = {
  heading: 'Sign-in failed',
  body: 'An error occurred during sign-in. Please try again or contact support if the problem persists.',
}

// For inline display on the sign-in page. CredentialsSignin is sign-in-only
// (Auth.js redirects it back to the sign-in page, not to /auth/error).
export const AUTH_ERROR_INLINE: Record<string, string> = {
  CredentialsSignin: 'Invalid credentials. Please try again.',
  OAuthAccountNotLinked:
    'An account with this email already exists but was created with a different sign-in method.',
  Default: 'Sign-in failed. Please try again.',
}

export function getInlineErrorMessage(code: string | null): string | null {
  if (!code) return null
  return AUTH_ERROR_INLINE[code] ?? AUTH_ERROR_INLINE.Default
}
