import {
  getWorkflowAnatomy,
  kernelError,
  KernelApiError,
} from '@/lib/api'
import { DescriptionForm } from './description-form'
import { DeleteWorkflowForm } from './delete-form'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Settings tab — workflow lifecycle controls that don't fit on
// Overview. Description edit + danger-zone delete for now. Will absorb
// Triggers / Integrations / Permissions when those mocks are wired
// (s26-rk7p3/12, 13, 14).
export default async function WorkflowSettingsPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let description: string | null = null
  let apiError: { title: string; detail: string } | null = null

  try {
    const anatomy = await getWorkflowAnatomy(wfId)
    description = anatomy.description
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  return (
    <>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      {description !== null ? (
        <div className="settings-stack">
          <DescriptionForm
            wsId={wsId}
            wfId={wfId}
            initialDescription={description}
          />
          <DeleteWorkflowForm wsId={wsId} wfId={wfId} />
        </div>
      ) : null}
    </>
  )
}
