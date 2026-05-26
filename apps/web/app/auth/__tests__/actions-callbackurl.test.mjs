/**
 * Tests for isSafeCallbackUrl — the server-action-level callbackUrl guard.
 *
 * Run with: node apps/web/app/auth/__tests__/actions-callbackurl.test.mjs
 *
 * Verifies that absolute URLs pointing at external origins are rejected and
 * that relative paths and same-origin URLs are accepted.
 */

import assert from 'node:assert/strict'

// Inline copy of isSafeCallbackUrl from actions.ts (avoids compilation).
// Must be kept in sync with the production implementation.
function isSafeCallbackUrl(url, authUrl = undefined) {
  if (url.startsWith('//')) return false
  if (url.startsWith('/')) return true
  const base = authUrl
  if (!base) return true // defer to Auth.js when AUTH_URL is unset
  try {
    return new URL(url).origin === new URL(base).origin
  } catch {
    return false
  }
}

const APP = 'https://app.ownevo.ai'

// Relative paths are always safe.
assert.ok(isSafeCallbackUrl('/', APP), 'root path')
assert.ok(isSafeCallbackUrl('/dashboard', APP), '/dashboard')
assert.ok(isSafeCallbackUrl('/workspaces/wf-123?tab=evals', APP), 'path with query string')

// Same-origin absolute URL is safe.
assert.ok(isSafeCallbackUrl('https://app.ownevo.ai/dashboard', APP), 'same-origin absolute URL')

// Cross-origin absolute URLs are rejected.
assert.ok(!isSafeCallbackUrl('https://evil.com', APP), 'external domain rejected')
assert.ok(!isSafeCallbackUrl('https://evil.com/phish', APP), 'external path rejected')
assert.ok(!isSafeCallbackUrl('https://app.ownevo.ai.evil.com/', APP), 'subdomain spoofing rejected')

// Protocol-relative and javascript: URLs are rejected (not a valid URL with same origin).
assert.ok(!isSafeCallbackUrl('//evil.com', APP), 'protocol-relative rejected')
assert.ok(!isSafeCallbackUrl('javascript:alert(1)', APP), 'javascript: URI rejected')

// When AUTH_URL is not set, all URLs are allowed (defer to Auth.js).
assert.ok(isSafeCallbackUrl('https://evil.com', undefined), 'when no AUTH_URL set, defer to Auth.js')

// callbackUrl fallback in the server action: raw → safe.
function applyCallbackFallback(raw, authUrl) {
  return isSafeCallbackUrl(raw, authUrl) ? raw : '/'
}
assert.equal(applyCallbackFallback('https://evil.com', APP), '/', 'unsafe URL falls back to /')
assert.equal(applyCallbackFallback('/dashboard', APP), '/dashboard', 'relative URL passes through')
assert.equal(
  applyCallbackFallback('https://app.ownevo.ai/dashboard', APP),
  'https://app.ownevo.ai/dashboard',
  'same-origin absolute URL passes through',
)

console.log('actions-callbackurl: all assertions passed')
