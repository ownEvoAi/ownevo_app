import Link from 'next/link'
import {
  getWorkflowAnatomy,
  getWorkflowIterations,
  getWorkflowSkills,
  kernelError,
  KernelApiError,
  listWorkflowEvalCases,
  type EvalCaseSummary,
  type IterationPoint,
  type SkillSummary,
  type WorkflowSpecShape,
} from '@/lib/api'
import { AgentAnatomy } from '@/app/components/agent-anatomy'
import { GenerateEvalCasesButton } from './eval-cases/generate-button'
import { RunIterationButton } from './run-iteration-button'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowOverviewPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let skills: SkillSummary[] = []
  let spec: WorkflowSpecShape | null = null
  let evalCases: EvalCaseSummary[] = []
  let iterations: IterationPoint[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    const [anatomy, skillList, evalList, iterList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowSkills(wfId),
      listWorkflowEvalCases(wfId),
      getWorkflowIterations(wfId),
    ])
    spec = anatomy.spec
    skills = skillList.items
    evalCases = evalList.items
    iterations = iterList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      apiError = { title: 'Workflow not registered.', detail: err.detail }
    } else {
      apiError = kernelError(err)
    }
  }

  const hasEvalCases = evalCases.length > 0
  const iterationCount = iterations.length
  const trainCount = evalCases.filter((c) => !c.is_test_fold).length
  const testCount = evalCases.length - trainCount
  const latestVal = iterationCount > 0 ? iterations[iterationCount - 1].val_score : null

  // Pick the next-step card content based on where the workflow is in the
  // gen → eval → iterate flow. Three stages, one card each.
  let stage: 'no-evals' | 'has-evals-no-iter' | 'iterating' = 'no-evals'
  if (hasEvalCases) {
    stage = iterationCount > 0 ? 'iterating' : 'has-evals-no-iter'
  }

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
              {stage === 'no-evals' && 'Next: generate eval cases'}
              {stage === 'has-evals-no-iter' && 'Next: run the first iteration'}
              {stage === 'iterating' && 'Improvement loop active'}
            </h3>
            <p className="overview-next-step-body">
              {stage === 'no-evals' && (
                <>
                  The improvement loop needs a test suite to score against.
                  Generate eval cases from the workflow&rsquo;s spec
                  (~30&ndash;60s, 2 LLM calls), then run an iteration.
                </>
              )}
              {stage === 'has-evals-no-iter' && (
                <>
                  This workflow has <strong>{evalCases.length}</strong> eval case
                  {evalCases.length === 1 ? '' : 's'} ({trainCount} train ·{' '}
                  {testCount} test). One iteration runs the agent against every
                  case, clusters its failures, and proposes an instruction edit
                  for the next round. ~30&ndash;90 seconds.
                </>
              )}
              {stage === 'iterating' && (
                <>
                  <strong>{iterationCount}</strong> iteration
                  {iterationCount === 1 ? '' : 's'} recorded
                  {latestVal !== null ? (
                    <>
                      {' '}· latest val_score{' '}
                      <code>{latestVal.toFixed(3)}</code>
                    </>
                  ) : null}{' '}
                  · {evalCases.length} eval cases in the suite. Each new
                  iteration re-runs the agent with the latest instruction and
                  proposes the next edit.
                </>
              )}
            </p>
          </div>
          <div className="overview-next-step-action">
            {stage === 'no-evals' && (
              <GenerateEvalCasesButton wsId={wsId} wfId={wfId} hasExisting={false} />
            )}
            {(stage === 'has-evals-no-iter' || stage === 'iterating') && (
              <RunIterationButton
                wsId={wsId}
                wfId={wfId}
                iterationCount={iterationCount}
              />
            )}
          </div>
        </section>
      ) : null}

      {!apiError && hasEvalCases ? (
        <p
          style={{
            marginTop: 12,
            fontSize: 12,
            color: 'var(--text-muted)',
            textAlign: 'right',
          }}
        >
          <Link
            href={`/workspaces/${wsId}/workflows/${wfId}/eval-cases`}
            style={{ color: 'var(--accent)' }}
          >
            View eval cases →
          </Link>
        </p>
      ) : null}
    </>
  )
}
