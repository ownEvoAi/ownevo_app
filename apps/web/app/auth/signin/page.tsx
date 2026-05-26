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
import { AuthShell } from '../_components/AuthShell'
import { getInlineErrorMessage } from '../_lib/error-messages'
import { signInWithGoogle } from './actions'
import { DevSignInForm } from './dev-signin-form'

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}) {
  const params = await searchParams
  const callbackUrl = typeof params.callbackUrl === 'string' ? params.callbackUrl : '/'
  const errorCode = typeof params.error === 'string' ? params.error : null
  const errorMessage = getInlineErrorMessage(errorCode)

  const devAuth =
    process.env.OWNEVO_DEV_AUTH?.toLowerCase() === 'true' &&
    !process.env.OWNEVO_INTERNAL_AUTH_KEY
  const hasGoogle = Boolean(process.env.AUTH_GOOGLE_ID && process.env.AUTH_GOOGLE_SECRET)

  const googleAction = signInWithGoogle.bind(null, callbackUrl)

  return (
    <AuthShell>
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
    </AuthShell>
  )
}
