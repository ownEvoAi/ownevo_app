import { getCopilotStudioStatus, kernelError, type CopilotStudioStatus } from '@/lib/api'
import { isDemoMode } from '@/lib/demo-mode'
import { CopilotStudioForm } from './copilot-studio-form'

interface PageProps {
 params: Promise<{ wsId: string }>
}

// Workspace-level Settings → Integrations → Copilot Studio.
//
// Single-tenant MVP: integration credentials are workspace-global
// (one row per provider), so this lives under the workspace settings
// area rather than a per-workflow tab. The credential is a structured
// Entra service principal (tenant / client id / secret / environment URL).
export default async function CopilotStudioIntegrationPage({ params }: PageProps) {
 const { wsId } = await params
 const demoMode = isDemoMode let status: CopilotStudioStatus | null = null
 let apiError: { title: string; detail: string } | null = null
 try {
 status = await getCopilotStudioStatus } catch (err) {
 apiError = kernelError(err)
 }

 return (
 <>
 <h1 className="page-title">Integrations · Copilot Studio</h1>
 {apiError && (
 <div role="alert" className="api-banner">
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )}
 {status && (
 <div className="settings-stack">
 <CopilotStudioForm wsId={wsId} initialStatus={status} demoMode={demoMode} />
 </div>
 )}
 </>
 )
}
