/**
 * Tests for the middleware auth-bypass path logic.
 *
 * Run with: node apps/web/app/auth/__tests__/middleware-paths.test.mjs
 *
 * Verifies that the allow-list is exact rather than prefix-based, preventing
 * future /auth/* routes from being accidentally public.
 */

import assert from 'node:assert/strict'

// Mirrors the bypass condition in middleware.ts.
// Update this when middleware.ts changes.
function isAuthBypassPath(pathname) {
  return (
    pathname.startsWith('/api/auth') ||
    pathname === '/auth/signin' ||
    pathname === '/auth/error'
  )
}

// Auth.js-owned routes — always bypass.
assert.ok(isAuthBypassPath('/api/auth/callback/google'), '/api/auth/callback/google')
assert.ok(isAuthBypassPath('/api/auth/signin'), '/api/auth/signin')
assert.ok(isAuthBypassPath('/api/auth/session'), '/api/auth/session')

// Custom auth pages — bypass.
assert.ok(isAuthBypassPath('/auth/signin'), '/auth/signin exact match')
assert.ok(isAuthBypassPath('/auth/error'), '/auth/error exact match')

// Future /auth/* routes must NOT be public by default.
assert.ok(!isAuthBypassPath('/auth/admin'), '/auth/admin must NOT be bypassed')
assert.ok(!isAuthBypassPath('/auth/debug'), '/auth/debug must NOT be bypassed')
assert.ok(!isAuthBypassPath('/auth/signin/extra'), '/auth/signin/extra must NOT be bypassed')

// Ordinary application routes — require auth.
assert.ok(!isAuthBypassPath('/dashboard'), '/dashboard')
assert.ok(!isAuthBypassPath('/'), '/')
assert.ok(!isAuthBypassPath('/setup/new-workspace'), '/setup/new-workspace')

// Prefix boundary: /auth without trailing slash is not a bypass path.
assert.ok(!isAuthBypassPath('/auth'), '/auth (no trailing slash) is NOT bypassed')

console.log('middleware-paths: all assertions passed')
