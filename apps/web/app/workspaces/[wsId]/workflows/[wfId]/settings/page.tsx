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
  // 'demo-mode' — kernel returned 503 because DEMO_MODE is on; the picker
  // is intentionally hidden on read-only deployments.
  // 'kernel-error' — any other failure; surface a recoverable banner.
  let catalogError: 'demo-mode' | 'kernel-error' | null = null

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
    const err = catalogResult.reason
    catalogError =
      err instanceof KernelApiError && err.status === 503
        ? 'demo-mode'
        : 'kernel-error'
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
          {catalogError === 'demo-mode' ? (
            <div className="settings-card">
              <div className="settings-card-header">
                <h2 className="settings-card-title">Agent model</h2>
                <p className="settings-card-subtitle">
                  Read-only deployment — model selection is disabled here. To
                  switch the per-workflow agent model, run the kernel locally
                  or on a non-demo deployment where{' '}
                  <code>DEMO_MODE</code> is unset.
                </p>
              </div>
            </div>
          ) : catalogError === 'kernel-error' ? (
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
          <div className="settings-card">
            <div className="settings-card-header">
              <h2 className="settings-card-title">Export</h2>
              <p className="settings-card-subtitle">
                Download a full ownership bundle or individual datasets as JSON.
                All exports are point-in-time snapshots — re-download to refresh.
              </p>
            </div>
            {/* Bundle — primary CTA */}
            <div style={{ marginTop: 12, marginBottom: 16 }}>
              <a
                href={`/workspaces/${wsId}/workflows/${wfId}/bundle/export`}
                className="btn btn-primary"
                download
              >
                Export full bundle
              </a>
              <p
                style={{
                  margin: '6px 0 0',
                  fontSize: 12,
                  color: 'var(--text-muted)',
                }}
              >
                Agent · evals · proposals · failures · audit in one file
              </p>
            </div>
            {/* Individual exports */}
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 8,
                paddingTop: 12,
                borderTop: '1px solid var(--border)',
              }}
            >
              <a
                href={`/workspaces/${wsId}/workflows/${wfId}/agent/export`}
                className="btn btn-secondary"
                download
              >
                Agent
              </a>
              <a
                href={`/workspaces/${wsId}/workflows/${wfId}/evals/export`}
                className="btn btn-secondary"
                download
              >
                Evals
              </a>
              <a
                href={`/workspaces/${wsId}/workflows/${wfId}/proposals/export`}
                className="btn btn-secondary"
                download
              >
                Proposals
              </a>
              <a
                href={`/workspaces/${wsId}/workflows/${wfId}/failures/export`}
                className="btn btn-secondary"
                download
              >
                Failures
              </a>
              <a
                href={`/workspaces/${wsId}/workflows/${wfId}/audit/export`}
                className="btn btn-secondary"
                download
              >
                Audit
              </a>
            </div>
          </div>
          <DeleteWorkflowForm wsId={wsId} wfId={wfId} />
        </div>
      ) : null}
    </>
  )
}
