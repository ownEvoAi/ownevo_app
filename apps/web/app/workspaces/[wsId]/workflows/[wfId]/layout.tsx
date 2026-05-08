import type { ReactNode } from 'react'
import { WorkflowTabs } from './workflow-tabs'

interface LayoutProps {
  children: ReactNode
  params: Promise<{ wsId: string; wfId: string }>
}

// Workflow-detail shell — header + tabs (Overview / Failures / Audit).
// Slices 3 + 4 wire Failures + Audit; slice 6 fills Overview for the
// three positioning mocks. Live demand-prediction Overview lands in
// W8.
export default async function WorkflowDetailLayout({ children, params }: LayoutProps) {
  const { wsId, wfId } = await params
  return (
    <>
      <div style={{ marginBottom: 18 }}>
        <a href={`/workspaces/${wsId}`} className="wf-back">
          ‹ Workflows
        </a>
        <div className="wf-title-row" style={{ marginTop: 6 }}>
          <h1 className="wf-title">{wfId}</h1>
        </div>
      </div>
      <WorkflowTabs wsId={wsId} wfId={wfId} />
      {children}
    </>
  )
}
