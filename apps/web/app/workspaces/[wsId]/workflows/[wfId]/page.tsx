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
import { LiftChart } from '../../lift-chart'
import { GenerateEvalCasesButton } from './eval-cases/generate-button'
import { InlineDescriptionBlock } from './inline-description-edit'
import { RunIterationButton } from './run-iteration-button'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowOverviewPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  // Overview is meta-only: it describes the workflow + tracks the
  // improvement loop's state. Live execution data (inputs flowing in,
  // outputs the agent produces) belongs on the Operate tab — that's
  // where the spec's `ui.tabs[].primitives` render. Overview shows:
  //   * a "next step" card driving the user toward the loop's next gate
  //   * the lift curve (val_score across iterations) — improvement-meta
  //   * the iteration list — improvement history
  //   * AgentAnatomy — what the agent CAN do (skills + tools + topology)
  let skills: SkillSummary[] = []
  let spec: WorkflowSpecShape | null = null
  let description: string = ''
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
    description = anatomy.description ?? ''
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

      {!apiError && description ? (
        <InlineDescriptionBlock
          wsId={wsId}
          wfId={wfId}
          description={description}
        />
      ) : null}

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

      {!apiError && iterations.length > 0 ? (
        <section style={{ marginTop: 20, marginBottom: 24 }}>
          <h2 className="section-title" style={{ marginBottom: 8 }}>
            Improvement curve
          </h2>
          <LiftChart points={iterations} workflowId={wfId} />
        </section>
      ) : null}

      {!apiError && iterations.length > 0 ? (
        <IterationList wsId={wsId} wfId={wfId} iterations={iterations} />
      ) : null}

      <AgentAnatomy wsId={wsId} workflowId={wfId} skills={skills} spec={spec} />

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

function IterationList({
  wsId,
  wfId,
  iterations,
}: {
  wsId: string
  wfId: string
  iterations: IterationPoint[]
}) {
  // Newest first — easier to scan the most recent runs.
  const rows = [...iterations].reverse()
  return (
    <section style={{ marginTop: 24 }}>
      <h2 className="section-title" style={{ marginBottom: 8 }}>
        Iterations · {iterations.length}
      </h2>
      <div className="iter-overview-list">
        <div className="iter-overview-row iter-overview-head">
          <span>Iter</span>
          <span>val_score</span>
          <span>Best ever</span>
          <span>State</span>
          <span>Approved?</span>
          <span>Ended</span>
        </div>
        {rows.map((it) => (
          <Link
            key={it.iteration_index}
            href={`/workspaces/${wsId}/workflows/${wfId}/iterations/${it.iteration_index}`}
            className="iter-overview-row"
          >
            <span className="iter-overview-idx">#{it.iteration_index}</span>
            <span className="iter-overview-num">
              {it.val_score !== null ? it.val_score.toFixed(3) : '—'}
            </span>
            <span className="iter-overview-num">
              {it.best_ever_score_after !== null
                ? it.best_ever_score_after.toFixed(3)
                : '—'}
            </span>
            <span className={`iter-overview-state state-${stateClass(it.state)}`}>
              {it.state}
            </span>
            <span className="iter-overview-approved">
              {it.has_approved_proposal ? '✓' : ''}
            </span>
            <span className="iter-overview-when">
              {it.ended_at ? new Date(it.ended_at).toISOString().slice(0, 16).replace('T', ' ') : '—'}
            </span>
          </Link>
        ))}
      </div>
    </section>
  )
}

function stateClass(state: string): string {
  if (state === 'gate-pass') return 'pass'
  if (state === 'gate-blocked-no-improvement') return 'blocked'
  if (state === 'gate-blocked-regression') return 'regression'
  if (state === 'sandbox-error') return 'error'
  return 'other'
}

// The full NL description (with inline-edit) lives in
// `./inline-description-edit.tsx`. Both Overview and Spec render it.
