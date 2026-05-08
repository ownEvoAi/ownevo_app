import {
  getWorkflowFailureClusters,
  KernelApiError,
  kernelErrorMessage,
  type FailureClusterList,
  type FailureClusterSummary,
} from '../../../../../../lib/api'
import { FailureClusterCard } from './failure-cluster-card'
import { getMockClusters, isMock } from '../mocks'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// W7 slice 3 — Failures view.
//
// One card per active cluster, sorted high → medium → low, then by
// cluster_size. Visual target: www/preview/s26-rk7p3/16-failures.html.
// Cluster cards are read-only for slice 3 — clicking a card lands on
// the proposal-detail page once cluster→proposal linkage is in place
// (W8 polish).
//
// Mock workflows (labour / contract / support) render hand-authored
// cluster fixtures from `mocks.ts` so the layout matches the Overview
// tab's STATIC MOCK banner instead of returning a 404 from the kernel.
export default async function WorkflowFailuresPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let clusters: FailureClusterList = { workflow_id: wfId, items: [] }
  let apiError: string | null = null
  let notFound = false

  if (isMock(wfId)) {
    const mockItems = getMockClusters(wfId) ?? []
    clusters = { workflow_id: wfId, items: mockItems as FailureClusterSummary[] }
  } else {
    try {
      clusters = await getWorkflowFailureClusters(wfId)
    } catch (err) {
      if (err instanceof KernelApiError && err.status === 404) {
        notFound = true
      } else {
        apiError = kernelErrorMessage(err)
      }
    }
  }

  // Group by severity to match the mock's "Active" / "Resolved" layout.
  const high = clusters.items.filter((c) => c.severity === 'high')
  const medium = clusters.items.filter((c) => c.severity === 'medium')
  const low = clusters.items.filter((c) => c.severity === 'low')

  return (
    <>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>Kernel API not reachable.</strong> {apiError}
        </div>
      )}

      {notFound && (
        <div role="alert" className="api-banner">
          <strong>Workflow not found.</strong> No workflow with id{' '}
          <code>{wfId}</code> in this workspace.
        </div>
      )}

      <div className="stats-row">
        <div className="card">
          <div className="card-title">Active clusters</div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 500,
              color: 'var(--text)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {clusters.items.length}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
            {high.length} high · {medium.length} medium · {low.length} low
          </div>
        </div>
        <div className="card">
          <div className="card-title">Total traces</div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 500,
              color: 'var(--text)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {clusters.items.reduce((acc, c) => acc + c.cluster_size, 0)}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
            across {clusters.items.length} cluster
            {clusters.items.length === 1 ? '' : 's'}
          </div>
        </div>
      </div>

      {clusters.items.length === 0 && !apiError && !notFound && (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 32,
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: 13,
            marginTop: 16,
          }}
        >
          No failure clusters yet for <code>{wfId}</code>. Run{' '}
          <code>make m5-cluster-failures</code> to populate them.
        </div>
      )}

      {high.length > 0 && (
        <>
          <div className="group-head">High · {high.length}</div>
          <div className="clusters">
            {high.map((c) => (
              <FailureClusterCard key={c.id} cluster={c} wsId={wsId} />
            ))}
          </div>
        </>
      )}

      {medium.length > 0 && (
        <>
          <div className="group-head">Medium · {medium.length}</div>
          <div className="clusters">
            {medium.map((c) => (
              <FailureClusterCard key={c.id} cluster={c} wsId={wsId} />
            ))}
          </div>
        </>
      )}

      {low.length > 0 && (
        <>
          <div className="group-head">Low · {low.length}</div>
          <div className="clusters">
            {low.map((c) => (
              <FailureClusterCard key={c.id} cluster={c} wsId={wsId} />
            ))}
          </div>
        </>
      )}
    </>
  )
}
