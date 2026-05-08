import { redirect } from 'next/navigation'

// W7 follow-up — the inbox migrated into the workspace shell at
// /workspaces/[wsId]/inbox to match www/preview/s26-rk7p3/02-inbox.html
// (workspace switcher + full nav, no layout swap when clicking Inbox
// from the Health page). This redirect preserves the original /inbox
// URL for any pre-W7 bookmarks.
//
// Slug is hardcoded to "acme" per the W7_SLICE.md cosmetic-URL
// decision; D4 single-tenant means the backend ignores it.
export default function LegacyInboxRedirect() {
  redirect('/workspaces/acme/inbox')
}
