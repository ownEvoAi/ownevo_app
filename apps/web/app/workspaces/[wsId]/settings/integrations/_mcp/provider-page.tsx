import {
  getMcpOAuthClient,
  kernelError,
  listMcpProviders,
  listMcpServers,
  type McpOAuthClientView,
  type McpProviderInfo,
  type McpServer,
} from '@/lib/api'
import { isDemoMode } from '@/lib/demo-mode'
import { McpProviderForm } from './mcp-provider-form'

// Shared server component for the three MCP provider settings pages
// (Slack / Google Workspace / Microsoft 365). Each route passes its provider
// id; everything else — fetching presets, OAuth client status, connected
// servers — is identical.
export async function McpProviderPage({
  wsId,
  provider,
  searchParams,
}: {
  wsId: string
  provider: string
  searchParams: { connected?: string; error?: string }
}) {
  const demoMode = isDemoMode()

  let info: McpProviderInfo | undefined
  let client: McpOAuthClientView | null = null
  let servers: McpServer[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    const [providers, clientView, allServers] = await Promise.all([
      listMcpProviders(),
      getMcpOAuthClient(provider),
      listMcpServers(),
    ])
    info = providers.find((p) => p.provider === provider)
    client = clientView
    servers = allServers.filter((s) => s.provider === provider)
  } catch (err) {
    apiError = kernelError(err)
  }

  const banner = searchParams.connected
    ? ({ kind: 'connected', detail: '' } as const)
    : searchParams.error
      ? ({ kind: 'error', detail: searchParams.error } as const)
      : null

  return (
    <>
      <h1 className="page-title">
        Integrations · {info?.display_name ?? provider}
      </h1>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}
      {info && client && (
        <McpProviderForm
          wsId={wsId}
          info={info}
          client={client}
          servers={servers}
          banner={banner}
          demoMode={demoMode}
        />
      )}
    </>
  )
}
