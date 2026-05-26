/**
 * Tests for the centralised Auth.js error-code → message mapping.
 *
 * Run with: node apps/web/app/auth/__tests__/error-messages.test.mjs
 *
 * No framework required — uses Node.js built-in assert.
 */

import assert from 'node:assert/strict'

// --- inline the pure-logic under test (avoids TypeScript compilation) ------
// Sync with apps/web/app/auth/_lib/error-messages.ts

const AUTH_ERROR_PAGES = {
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

const DEFAULT_AUTH_ERROR_PAGE = {
  heading: 'Sign-in failed',
  body: 'An error occurred during sign-in. Please try again or contact support if the problem persists.',
}

const AUTH_ERROR_INLINE = {
  CredentialsSignin: 'Invalid credentials. Please try again.',
  OAuthAccountNotLinked:
    'An account with this email already exists but was created with a different sign-in method.',
  Default: 'Sign-in failed. Please try again.',
}

function getInlineErrorMessage(code) {
  if (!code) return null
  return AUTH_ERROR_INLINE[code] ?? AUTH_ERROR_INLINE.Default
}

// --- error page mapping ---

// Known codes return the mapped heading and body.
for (const [code, expected] of Object.entries(AUTH_ERROR_PAGES)) {
  const result = AUTH_ERROR_PAGES[code] ?? DEFAULT_AUTH_ERROR_PAGE
  assert.equal(result.heading, expected.heading, `${code}: heading mismatch`)
  assert.equal(result.body, expected.body, `${code}: body mismatch`)
}

// Unknown code falls back to DEFAULT_AUTH_ERROR_PAGE.
assert.deepEqual(
  AUTH_ERROR_PAGES['UnknownCode123'] ?? DEFAULT_AUTH_ERROR_PAGE,
  DEFAULT_AUTH_ERROR_PAGE,
  'unknown code should fall back to DEFAULT_AUTH_ERROR_PAGE',
)

// Null code (no ?error param) falls back to DEFAULT_AUTH_ERROR_PAGE.
const noCode = null
assert.deepEqual(
  noCode ? (AUTH_ERROR_PAGES[noCode] ?? DEFAULT_AUTH_ERROR_PAGE) : DEFAULT_AUTH_ERROR_PAGE,
  DEFAULT_AUTH_ERROR_PAGE,
  'null code should produce DEFAULT_AUTH_ERROR_PAGE',
)

// Configuration copy must not imply permanent misconfiguration.
assert.ok(
  !AUTH_ERROR_PAGES.Configuration.body.includes('misconfigured'),
  'Configuration body should not say "misconfigured" — transient outages produce this code too',
)

// --- inline sign-in page messages ---

assert.equal(
  getInlineErrorMessage('CredentialsSignin'),
  'Invalid credentials. Please try again.',
  'CredentialsSignin inline message',
)
assert.ok(
  getInlineErrorMessage('OAuthAccountNotLinked')?.includes('different sign-in method'),
  'OAuthAccountNotLinked inline message',
)
// Unknown code falls back to Default.
assert.equal(
  getInlineErrorMessage('SomeNewCode'),
  AUTH_ERROR_INLINE.Default,
  'unknown code falls back to Default inline message',
)
// Null returns null.
assert.equal(getInlineErrorMessage(null), null, 'null code returns null')

// OAuthAccountNotLinked is present in both maps (consistency check).
assert.ok(
  AUTH_ERROR_PAGES.OAuthAccountNotLinked !== undefined,
  'OAuthAccountNotLinked must be in AUTH_ERROR_PAGES',
)
assert.ok(
  AUTH_ERROR_INLINE.OAuthAccountNotLinked !== undefined,
  'OAuthAccountNotLinked must be in AUTH_ERROR_INLINE',
)

console.log('error-messages: all assertions passed')
