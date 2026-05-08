import { redirect } from 'next/navigation'

interface PageProps {
  searchParams: Promise<{ workflow_id?: string }>
}

// W7 slice 5 — `/workflows/preview` moved into the workspace shell at
// `/workspaces/[wsId]/workflows/new`. This redirect preserves any
// existing bookmarks (W5.5 launch demos, internal links) while
// pointing new traffic at the workspace-scoped surface.
//
// Slug is hardcoded to "acme" per the W7_SLICE.md cosmetic-URL
// decision; D4 single-tenant means the backend ignores the value.
export default async function LegacyPreviewRedirect({ searchParams }: PageProps) {
  const { workflow_id } = await searchParams
  const target =
    workflow_id !== undefined
      ? `/workspaces/acme/workflows/new?workflow_id=${encodeURIComponent(workflow_id)}`
      : `/workspaces/acme/workflows/new`
  redirect(target)
}
