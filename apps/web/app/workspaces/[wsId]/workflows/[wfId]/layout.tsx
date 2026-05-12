import type { ReactNode } from 'react'
import {
  getWorkflowAnatomy,
  KernelApiError,
} from '@/lib/api'
import { workflowDisplayTitle } from '@/lib/format'
import { WorkflowTabs } from './workflow-tabs'

interface LayoutProps {
  children: ReactNode
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowDetailLayout({ children, params }: LayoutProps) {
  const { wsId, wfId } = await params

  let title = wfId
  let subtitle: string | null = null
  let notFound = false
  try {
    const anatomy = await getWorkflowAnatomy(wfId)
    title = workflowDisplayTitle(anatomy.id, anatomy.description, 100)
    subtitle = `${anatomy.mode === 'gated' ? 'Gated' : 'Autonomous'} · ${anatomy.id}`
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
        <div className="wf-title-row" style={{ marginTop: 6 }}>
          <h1 className="wf-title">{title}</h1>
        </div>
        {subtitle && !notFound ? (
          <p className="wf-buyer" style={{ marginTop: 6 }}>
            {subtitle}
          </p>
        ) : null}
      </div>
      <WorkflowTabs wsId={wsId} wfId={wfId} />
      {children}
    </>
  )
}
