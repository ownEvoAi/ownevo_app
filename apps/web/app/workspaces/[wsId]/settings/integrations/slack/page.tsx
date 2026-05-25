import { McpProviderPage } from '../_mcp/provider-page'

interface PageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ connected?: string; error?: string }>
}

export default async function SlackIntegrationPage({ params, searchParams }: PageProps) {
  const { wsId } = await params
  const sp = await searchParams
  return <McpProviderPage wsId={wsId} provider="slack" searchParams={sp} />
}
