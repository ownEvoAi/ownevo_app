import { redirect } from 'next/navigation'

interface PageProps {
  params: Promise<{ id: string }>
}

// W7 slice 7 (7.1.4) — `/proposals/[id]` moved into the workspace
// shell at `/workspaces/[wsId]/proposals/[id]`. This redirect
// preserves the W5.1 launch links + the `make demo-print-link`
// kernel output (apps/web/README.md § Local development) while new
// traffic lands on the workspace-scoped surface.
//
// Slug is hardcoded to "acme" per W7_SLICE.md cosmetic-URL decision;
// D4 single-tenant means the backend ignores the value.
export default async function LegacyProposalRedirect({ params }: PageProps) {
  const { id } = await params
  redirect(`/workspaces/acme/proposals/${id}`)
}
