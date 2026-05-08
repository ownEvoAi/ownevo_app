import type { ReactNode } from 'react'
import { getMock } from './mocks'
import { WorkflowTabs } from './workflow-tabs'

interface LayoutProps {
  children: ReactNode
  params: Promise<{ wsId: string; wfId: string }>
}

// Workflow-detail shell — breadcrumb + title row + tabs.
// For the three positioning mocks (labour / contract / support) the
// title comes from mocks.ts; for live workflows (demand-prediction
// after slice 5 + W8) the title is the wfId itself for now. Slice 6
// also adds a small <MockBanner /> when the wfId is a mock so
// reviewers don't confuse positioning copy with live data.
export default async function WorkflowDetailLayout({ children, params }: LayoutProps) {
  const { wsId, wfId } = await params
  const mock = getMock(wfId)

  const statusPill = mock ? STATUS_PILL[mock.status] : null

  return (
    <>
      <div style={{ marginBottom: 18 }}>
        <a href={`/workspaces/${wsId}`} className="wf-back">
          ‹ Workflows
        </a>
        <div className="wf-title-row" style={{ marginTop: 6 }}>
          <h1 className="wf-title">{mock?.title ?? wfId}</h1>
          {mock && (
            <>
              <span className={`pill ${statusPill?.tone ?? 'outline'}`}>
                {statusPill?.label ?? mock.status}
              </span>
              <span className="pill outline">{mock.version}</span>
            </>
          )}
        </div>
        {mock ? (
          <p className="wf-buyer" style={{ marginTop: 6 }}>
            Owner: {mock.buyer} · {mock.buyerRole} · {mock.description}
          </p>
        ) : (
          <p className="wf-buyer" style={{ marginTop: 6 }}>
            Live workflow · backed by the kernel API
          </p>
        )}
        {mock && (
          <div className="mock-banner" role="note" style={{ marginTop: 12 }}>
            <strong>STATIC MOCK</strong> · positioning copy for the four-workflow tab strip ·
            same loop, NL-gen the rest
          </div>
        )}
      </div>
      <WorkflowTabs wsId={wsId} wfId={wfId} />
      {children}
    </>
  )
}

const STATUS_PILL: Record<string, { tone: string; label: string }> = {
  active: { tone: 'green', label: '● Active' },
  pilot: { tone: 'amber', label: '● Pilot' },
  paused: { tone: 'outline', label: '● Paused' },
}
