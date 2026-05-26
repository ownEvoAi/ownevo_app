import type { SearchParams } from 'next/dist/server/request/search-params'
import Link from 'next/link'
import { AuthShell } from '../_components/AuthShell'
import { AUTH_ERROR_PAGES, DEFAULT_AUTH_ERROR_PAGE } from '../_lib/error-messages'

export default async function AuthErrorPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}) {
  const params = await searchParams
  const errorCode = typeof params.error === 'string' ? params.error : null
  const { heading, body } = errorCode
    ? (AUTH_ERROR_PAGES[errorCode] ?? DEFAULT_AUTH_ERROR_PAGE)
    : DEFAULT_AUTH_ERROR_PAGE

  return (
    <AuthShell>
      <h1 className="setup-title">{heading}</h1>
      <p className="setup-body">{body}</p>
      <Link
        href="/auth/signin"
        className="setup-submit"
        style={{ marginTop: 24, textAlign: 'center', display: 'block' }}
      >
        Back to sign in
      </Link>
    </AuthShell>
  )
}
