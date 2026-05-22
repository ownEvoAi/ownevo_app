import {
  getModelCatalog,
  getWorkflowAnatomy,
  kernelError,
  KernelApiError,
  type ProviderModels,
} from '@/lib/api'
import { DescriptionForm } from './description-form'
import { DeleteWorkflowForm } from './delete-form'
import { ModelPickerForm } from './model-picker-form'

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
  let agentModelId: string | null = null
  let providers: ProviderModels[] = []
  let apiError: { title: string; detail: string } | null = null
  let catalogError = false

  // Use allSettled so a /api/models glitch doesn't take down the
  // description form — each fetch fails independently.
  const [anatomyResult, catalogResult] = await Promise.allSettled([
    getWorkflowAnatomy(wfId),
    getModelCatalog(),
  ])

  if (anatomyResult.status === 'fulfilled') {
    description = anatomyResult.value.description
    agentModelId = anatomyResult.value.agent_model_id
  } else {
    const err = anatomyResult.reason
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  if (catalogResult.status === 'fulfilled') {
    providers = catalogResult.value.providers
  } else {
    catalogError = true
  }

  return (
    <>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      {description !== null && agentModelId !== null ? (
        <div className="settings-stack">
          <DescriptionForm
            wsId={wsId}
            wfId={wfId}
            initialDescription={description}
          />
          {catalogError ? (
            <div role="alert" className="api-banner">
              <strong>Model catalog unavailable.</strong> Could not load the
              provider list from the kernel. Restart the kernel or check logs.
            </div>
          ) : (
            <ModelPickerForm
              wsId={wsId}
              wfId={wfId}
              initialAgentModelId={agentModelId}
              providers={providers}
            />
          )}
          <DeleteWorkflowForm wsId={wsId} wfId={wfId} />
        </div>
      ) : null}
    </>
  )
}
