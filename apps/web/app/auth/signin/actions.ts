'use server'
import { AuthError } from 'next-auth'
import { redirect } from 'next/navigation'
import { signIn } from '@/auth'

export type SignInResult = { error?: string }

// Validates that a callbackUrl is safe to redirect to after sign-in.
// Relative paths (excluding protocol-relative //host) are always safe.
// Absolute URLs must match AUTH_URL's origin. When AUTH_URL is not set,
// defers to Auth.js's own redirectTo validation.
function isSafeCallbackUrl(url: string): boolean {
  // Protocol-relative URLs (//evil.com) look like relative paths but resolve
  // to an absolute external URL in browser contexts — always reject them.
  if (url.startsWith('//')) return false
  if (url.startsWith('/')) return true
  const base = process.env.AUTH_URL ?? process.env.NEXTAUTH_URL
  if (!base) return true
  try {
    return new URL(url).origin === new URL(base).origin
  } catch {
    return false
  }
}

export async function signInWithDev(
  _prev: SignInResult | null,
  formData: FormData,
): Promise<SignInResult> {
  const email =
    typeof formData.get('email') === 'string'
      ? (formData.get('email') as string).trim()
      : ''
  const rawCallbackUrl =
    typeof formData.get('callbackUrl') === 'string'
      ? (formData.get('callbackUrl') as string)
      : '/'
  const callbackUrl = isSafeCallbackUrl(rawCallbackUrl) ? rawCallbackUrl : '/'
  try {
    await signIn('dev', { email: email || 'dev@ownevo.local', redirectTo: callbackUrl })
    // signIn() redirects internally on success — this line is never reached.
    return {}
  } catch (err) {
    // Re-throw redirects (NEXT_REDIRECT); only swallow auth errors.
    if (err instanceof AuthError) {
      return { error: 'Sign-in failed. Please try again.' }
    }
    throw err
  }
}

export async function signInWithGoogle(callbackUrl: string): Promise<void> {
  const safeCallbackUrl = isSafeCallbackUrl(callbackUrl) ? callbackUrl : '/'
  try {
    await signIn('google', { redirectTo: safeCallbackUrl })
  } catch (err) {
    if (err instanceof AuthError) {
      redirect(`/auth/error?error=OAuthSignin`)
    }
    throw err
  }
}
