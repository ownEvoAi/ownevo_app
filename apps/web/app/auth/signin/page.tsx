// Sign-in page — shown to unauthenticated users by the middleware redirect and
// by Auth.js when it needs the user to authenticate.
//
// Providers are registered only when their prerequisites are present:
//   dev  — OWNEVO_DEV_AUTH=true (no signing key)
//   google — AUTH_GOOGLE_ID + AUTH_GOOGLE_SECRET set
//
// Each provider renders its own form section. The callbackUrl is threaded
// through so the user lands back at the page they were trying to reach.
import type { SearchParams } from 'next/dist/server/request/search-params'
import { signInWithGoogle } from './actions'
import { DevSignInForm } from './dev-signin-form'

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

// Auth.js passes ?error=<code> when a sign-in attempt fails and redirects
// back to the sign-in page. Map known codes to messages the domain expert
// will understand.
const SIGNIN_ERROR_MESSAGES: Record<string, string> = {
  CredentialsSignin: 'Invalid credentials. Please try again.',
  OAuthAccountNotLinked:
    'An account with this email already exists but was created with a different sign-in method.',
  Default: 'Sign-in failed. Please try again.',
}

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}) {
  const params = await searchParams
  const callbackUrl = typeof params.callbackUrl === 'string' ? params.callbackUrl : '/'
  const errorCode = typeof params.error === 'string' ? params.error : null
  const errorMessage = errorCode
    ? (SIGNIN_ERROR_MESSAGES[errorCode] ?? SIGNIN_ERROR_MESSAGES.Default)
    : null

  const devAuth =
    process.env.OWNEVO_DEV_AUTH?.toLowerCase() === 'true' &&
    !process.env.OWNEVO_INTERNAL_AUTH_KEY
  const hasGoogle = Boolean(process.env.AUTH_GOOGLE_ID && process.env.AUTH_GOOGLE_SECRET)

  const googleAction = signInWithGoogle.bind(null, callbackUrl)

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

        <h1 className="setup-title">Sign in</h1>
        <p className="setup-body">Sign in to access your ownEvo workspace.</p>

        {errorMessage && <p className="setup-error" style={{ marginBottom: 8 }}>{errorMessage}</p>}

        {devAuth && (
          <>
            <p className="setup-label" style={{ marginBottom: 12 }}>
              Dev credentials
            </p>
            <DevSignInForm callbackUrl={callbackUrl} />
          </>
        )}

        {hasGoogle && (
          <form action={googleAction} style={{ marginTop: devAuth ? 20 : 0 }}>
            <button type="submit" className="setup-submit" style={{ width: '100%' }}>
              Sign in with Google
            </button>
          </form>
        )}

        {!devAuth && !hasGoogle && (
          <p className="setup-error">
            No sign-in providers are configured. Check the deployment environment.
          </p>
        )}
      </div>
    </div>
  )
}
