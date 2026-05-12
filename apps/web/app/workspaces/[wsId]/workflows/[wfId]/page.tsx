import {
  getWorkflowAnatomy,
  getWorkflowSkills,
  kernelError,
  KernelApiError,
  type SkillSummary,
  type WorkflowSpecShape,
} from '@/lib/api'
import { AgentAnatomy } from '@/app/components/agent-anatomy'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowOverviewPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let skills: SkillSummary[] = []
  let spec: WorkflowSpecShape | null = null
  let apiError: { title: string; detail: string } | null = null
  try {
    const [anatomy, skillList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowSkills(wfId),
    ])
    spec = anatomy.spec
    skills = skillList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  const primitivesPlanned = (spec?.ui?.tabs?.[0]?.primitives ?? []).length

  return (
    <>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      <AgentAnatomy wsId={wsId} workflowId={wfId} skills={skills} spec={spec} />

      {!apiError ? (
        <section
          style={{
            marginTop: 24,
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 20,
            color: 'var(--text-muted)',
            fontSize: 13,
            lineHeight: 1.55,
          }}
        >
          {primitivesPlanned > 0 ? (
            <>
              <strong>No iteration data yet.</strong> The spec declares{' '}
              {primitivesPlanned} render primitive{primitivesPlanned === 1 ? '' : 's'}{' '}
              for this workflow&rsquo;s Overview. Run an iteration to populate the
              metric cards, charts, and tables with real agent output.
            </>
          ) : (
            <>
              <strong>No render primitives configured.</strong> This workflow&rsquo;s
              spec has no <code>ui.tabs[0].primitives</code> block. Regenerate the spec
              or add primitives to surface metrics on the Overview tab.
            </>
          )}
        </section>
      ) : null}
    </>
  )
}
