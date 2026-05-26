// Auth error page — shown by Auth.js when authentication fails in a way that
// cannot be recovered on the sign-in page (OAuth errors, access denied, etc.).
//
// Auth.js passes ?error=<ErrorCode> as a URL param. Known codes and their
// user-facing messages are listed below; unknown codes fall back to a generic
// message with a link back to sign-in.
import type { SearchParams } from 'next/dist/server/request/search-params'
import Link from 'next/link'

const ERROR_MESSAGES: Record<string, { heading: string; body: string }> = {
  AccessDenied: {
    heading: 'Access denied',
    body:
      "Your Google account's email address is not verified. Please verify your email with Google and try again.",
  },
  OAuthSignin: {
    heading: 'Sign-in error',
    body: 'There was a problem starting the sign-in flow. Please try again.',
  },
  OAuthCallback: {
    heading: 'Sign-in error',
    body:
      'There was a problem completing the Google sign-in. This can happen if the browser tab was closed mid-flow. Please try again.',
  },
  OAuthAccountNotLinked: {
    heading: 'Account conflict',
    body:
      'An account with this email already exists but was created with a different sign-in method. Please sign in with the original method.',
  },
  Configuration: {
    heading: 'Configuration error',
    body: 'The authentication service is misconfigured. Please contact the workspace administrator.',
  },
  Verification: {
    heading: 'Link expired',
    body: 'The sign-in link has expired or has already been used. Please request a new one.',
  },
}

const DEFAULT_ERROR = {
  heading: 'Sign-in failed',
  body: 'An error occurred during sign-in. Please try again or contact support if the problem persists.',
}

const BRAND_MARK = (
  <svg className="setup-brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden>
    <path
      d="M12 1.75 L20.25 4.75 V12 C20.25 17 16.5 20.75 12 22.25 C7.5 20.75 3.75 17 3.75 12 V4.75 Z"
      fill="#3b82f6"
    />
    <circle cx="12" cy="12.5" r="3.2" stroke="#07090e" strokeWidth="2" />
    <path d="M9.6 7 L12 4.5 L14.4 7 Z" fill="#07090e" />
  </svg>
)

export default async function AuthErrorPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}) {
  const params = await searchParams
  const errorCode = typeof params.error === 'string' ? params.error : null
  const { heading, body } = errorCode
    ? (ERROR_MESSAGES[errorCode] ?? DEFAULT_ERROR)
    : DEFAULT_ERROR

  return (
    <div className="setup-shell">
      <div className="setup-card">
        <div className="setup-brand">
          {BRAND_MARK}
          <span className="setup-brand-name">
            <span className="logo-own">own</span>
            <span className="logo-evo">Evo</span>
          </span>
        </div>

        <h1 className="setup-title">{heading}</h1>
        <p className="setup-body">{body}</p>

        <Link href="/auth/signin" className="setup-submit" style={{ marginTop: 24, textAlign: 'center', display: 'block' }}>
          Back to sign in
        </Link>
      </div>
    </div>
  )
}
