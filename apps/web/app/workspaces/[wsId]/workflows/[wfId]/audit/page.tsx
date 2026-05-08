import { redirect } from 'next/navigation'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Workflow-scoped audit isn't a separate surface yet — the
// audit_entries table is workspace-level (D4 single-tenant) and a
// `related_id`-driven filter would need to chain through proposals →
// iterations → workflow_id. For W7 slice 4 the workflow tab redirects
// to the workspace audit; slice 6 or W8 polish can add the per-workflow
// filter once the scope is justified.
export default async function WorkflowAuditPage({ params }: PageProps) {
  const { wsId } = await params
  redirect(`/workspaces/${wsId}/audit`)
}
