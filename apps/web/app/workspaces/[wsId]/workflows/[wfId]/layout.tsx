import type { ReactNode } from 'react'
import {
 getWorkflowAnatomy,
 KernelApiError,
} from '@/lib/api'
import { modeLabel, workflowDisplayTitle } from '@/lib/format'
import { WorkflowTabs } from './workflow-tabs'

interface LayoutProps {
 children: ReactNode
 params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowDetailLayout({ children, params }: LayoutProps) {
 const { wsId, wfId } = await params

 let title = wfId
 let subtitle: string | null = null
 let isBenchmark = false
 let notFound = false
 try {
 const anatomy = await getWorkflowAnatomy(wfId)
 title = workflowDisplayTitle(anatomy.id, anatomy.description, 100)
 subtitle = `${modeLabel(anatomy.mode).label} · ${anatomy.id}`
 isBenchmark = anatomy.kind === 'benchmark'
 } catch (err) {
 if (err instanceof KernelApiError && err.status === 404) {
 notFound = true
 }
 }

 return (
 <>
 <div style={{ marginBottom: 18 }}>
 <a href={`/workspaces/${wsId}`} className="wf-back">
 ‹ Workflows
 </a>
 <div
 className="wf-title-row"
 style={{ marginTop: 6, justifyContent: 'space-between' }}
 >
 <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
 <h1 className="wf-title">{title}</h1>
 {isBenchmark && (
 <span
 className="pill benchmark-pill"
 title="Kernel validation run — not a customer workflow"
 >
 BENCHMARK
 </span>
 )}
 </div>
 {!notFound ? (
 <a
 href={`/operator/${wfId}?ws=${encodeURIComponent(wsId)}`}
 className="btn btn-secondary"
 style={{ fontSize: 12, padding: '6px 12px' }}
 >
 Open operator view ↗
 </a>
 ) : null}
 </div>
 {subtitle && !notFound ? (
 <p className="wf-buyer" style={{ marginTop: 6 }}>
 {subtitle}
 </p>
 ) : null}
 </div>
 <WorkflowTabs wsId={wsId} wfId={wfId} isBenchmark={isBenchmark} />
 {children}
 </>
 )
}
