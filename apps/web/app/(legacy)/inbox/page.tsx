import { redirect } from 'next/navigation'

// follow-up — the inbox migrated into the workspace shell at
// /workspaces/[wsId]/inbox to match
// (workspace switcher + full nav, no layout swap when clicking Inbox
// from the Health page). This redirect preserves the original /inbox
// URL for any pre- bookmarks.
//
// Slug is hardcoded to "acme" per the W7_SLICE.md cosmetic-URL
// decision; D4 single-tenant means the backend ignores it.
export default function LegacyInboxRedirect {
 redirect('/workspaces/acme/inbox')
}
