'use server'
import { AuthError } from 'next-auth'
import { signIn } from '@/auth'

export type SignInResult = { error?: string }

export async function signInWithDev(
  _prev: SignInResult | null,
  formData: FormData,
): Promise<SignInResult> {
  const email =
    typeof formData.get('email') === 'string'
      ? (formData.get('email') as string).trim()
      : ''
  const callbackUrl =
    typeof formData.get('callbackUrl') === 'string'
      ? (formData.get('callbackUrl') as string)
      : '/'
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
  // Redirects to Google; never returns normally.
  await signIn('google', { redirectTo: callbackUrl })
}
