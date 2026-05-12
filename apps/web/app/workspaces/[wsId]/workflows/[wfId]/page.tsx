import Link from 'next/link'
import {
  getWorkflowAnatomy,
  getWorkflowSkills,
  kernelError,
  KernelApiError,
  listWorkflowEvalCases,
  type EvalCaseSummary,
  type SkillSummary,
  type WorkflowSpecShape,
} from '@/lib/api'
import { AgentAnatomy } from '@/app/components/agent-anatomy'
import { GenerateEvalCasesButton } from './eval-cases/generate-button'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowOverviewPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let skills: SkillSummary[] = []
  let spec: WorkflowSpecShape | null = null
  let evalCases: EvalCaseSummary[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    const [anatomy, skillList, evalList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowSkills(wfId),
      listWorkflowEvalCases(wfId),
    ])
    spec = anatomy.spec
    skills = skillList.items
    evalCases = evalList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  const primitivesPlanned = (spec?.ui?.tabs?.[0]?.primitives ?? []).length
  const hasEvalCases = evalCases.length > 0
  const trainCount = evalCases.filter((c) => !c.is_test_fold).length
  const testCount = evalCases.length - trainCount

  return (
    <>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      <AgentAnatomy wsId={wsId} workflowId={wfId} skills={skills} spec={spec} />

      {!apiError ? (
        <section className="overview-next-step">
          <div className="overview-next-step-text">
            <h3 className="overview-next-step-title">
              {hasEvalCases ? 'Eval cases ready' : 'Next: generate eval cases'}
            </h3>
            <p className="overview-next-step-body">
              {hasEvalCases ? (
                <>
                  This workflow has <strong>{evalCases.length}</strong> eval case
                  {evalCases.length === 1 ? '' : 's'} ({trainCount} train ·{' '}
                  {testCount} test). The improvement loop scores proposed
                  changes against them.
                </>
              ) : (
                <>
                  The improvement loop needs a test suite to score against.
                  Generate eval cases from the workflow&rsquo;s spec
                  (~30&ndash;60s, 2 LLM calls), then run an iteration.
                </>
              )}
            </p>
            {primitivesPlanned > 0 ? (
              <p className="overview-next-step-meta">
                Spec declares {primitivesPlanned} render primitive
                {primitivesPlanned === 1 ? '' : 's'}. They fill in once an
                iteration has run.
              </p>
            ) : null}
          </div>
          <div className="overview-next-step-action">
            {hasEvalCases ? (
              <Link
                href={`/workspaces/${wsId}/workflows/${wfId}/eval-cases`}
                className="btn btn-secondary"
              >
                View eval cases &rsaquo;
              </Link>
            ) : (
              <GenerateEvalCasesButton wsId={wsId} wfId={wfId} hasExisting={false} />
            )}
          </div>
        </section>
      ) : null}
    </>
  )
}
