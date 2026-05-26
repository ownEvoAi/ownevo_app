import { redirect } from 'next/navigation'

// Legacy `/workflows/preview` URL — moved into the workspace shell as
// `/workspaces/[wsId]/workflows/new`. This redirect preserves any
// existing bookmarks. The 8.4.3 rewrite dropped the `?workflow_id=`
// query param (the preview-fixture picker is gone); the new form is
// purely a textarea, so the redirect lands on the bare URL.
export default async function LegacyPreviewRedirect {
 redirect('/workspaces/acme/workflows/new')
}
