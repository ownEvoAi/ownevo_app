import { getLangSmithStatus, kernelError, type LangSmithStatus } from '@/lib/api'
import { isDemoMode } from '@/lib/demo-mode'
import { LangSmithForm } from './langsmith-form'

interface PageProps {
 params: Promise<{ wsId: string }>
}

// Workspace-level Settings → Integrations → LangSmith.
//
// Single-tenant MVP: integration credentials are workspace-global
// (one row per provider), so this lives under the workspace settings
// area rather than a per-workflow tab.
export default async function LangSmithIntegrationPage({ params }: PageProps) {
 const { wsId } = await params
 const demoMode = isDemoMode()
 let status: LangSmithStatus | null = null
 let apiError: { title: string; detail: string } | null = null
 try {
 status = await getLangSmithStatus()
 } catch (err) {
 apiError = kernelError(err)
 }

 return (
 <>
 <h1 className="page-title">Integrations · LangSmith</h1>
 {apiError && (
 <div role="alert" className="api-banner">
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )}
 {status && (
 <div className="settings-stack">
 <LangSmithForm wsId={wsId} initialStatus={status} demoMode={demoMode} />
 </div>
 )}
 </>
 )
}
