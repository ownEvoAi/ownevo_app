// Mock parity: s26-rk7p3/14-workflow-permissions.html. Surface present;
// real role assignment (Reviewers / Observers / Operators) and
// system-prompt edit waits on the auth + multi-tenant retrofit (D4 —
// MVP is single-tenant).
import {
  getWorkflowAnatomy,
  kernelError,
  KernelApiError,
  type WorkflowSpecShape,
} from '@/lib/api'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowPermissionsPage({ params }: PageProps) {
  const { wsId: _wsId, wfId } = await params

  let spec: WorkflowSpecShape | null = null
  let apiError: { title: string; detail: string } | null = null
  try {
    const anatomy = await getWorkflowAnatomy(wfId)
    spec = anatomy.spec
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  const reviewerRole = spec?.reviewer?.role ?? 'Reviewer'
  const reviewerCadence = spec?.reviewer?.cadence ?? '—'

  return (
    <>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      <div className="planned-tab">
        <div className="planned-tab-pill">Partially wired</div>
        <h2 className="planned-tab-title">Permissions</h2>
        <p className="planned-tab-body">
          Who can do what on this workflow. The MVP is single-tenant
          (D4) so the live RBAC surface is one row — the reviewer
          declared on the workflow spec. The multi-tenant retrofit
          (multi-tenant retrofit, before customer #2) lights up the full surface.
        </p>

        <div className="planned-tab-card" style={{ marginTop: 14 }}>
          <div className="planned-tab-card-head">Declared reviewer (from spec)</div>
          <div className="planned-tab-card-body">
            <div className="planned-tab-kv">
              <span>Role</span>
              <strong>{reviewerRole}</strong>
            </div>
            <div className="planned-tab-kv">
              <span>Cadence</span>
              <strong>{reviewerCadence}</strong>
            </div>
            <div className="planned-tab-kv">
              <span>Description</span>
              <span>{spec?.reviewer?.description || '—'}</span>
            </div>
          </div>
        </div>

        <p className="planned-tab-body" style={{ marginTop: 16 }}>
          Planned, once auth + multi-tenant lands:
        </p>
        <ul className="planned-tab-list">
          <li>
            <strong>Reviewers</strong> — approve / reject / request
            changes; receive proposals in their inbox.
          </li>
          <li>
            <strong>Observers</strong> — read-only access to the
            workflow shell + audit log.
          </li>
          <li>
            <strong>Operators</strong> — log into the operator shell to
            review what the agent has produced; can&rsquo;t change spec.
          </li>
          <li>
            <strong>System prompt</strong> — workflow-scoped prompt the
            agent runs with; today, the prompt is the cumulative
            instruction the iteration runner builds.
          </li>
        </ul>
      </div>
    </>
  )
}
