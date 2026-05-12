import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  getWorkflowAnatomy,
  getWorkflowSkills,
  KernelApiError,
  listWorkflowEvalCases,
  type EvalCaseSummary,
  type SkillSummary,
  type WorkflowSpecShape,
} from '@/lib/api'
import { AgentAnatomy } from '@/app/components/agent-anatomy'
import { GenerateEvalCasesButton } from '../../../[wfId]/eval-cases/generate-button'
import { ReviseButton } from './revise-button'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Step 2 of the new-workflow flow — review what NL-gen produced before
// the loop starts spending tokens against it. The kernel committed the
// row at the end of step 1 (spec + sim_plan + metric_definition are in
// DB), but eval cases haven't been generated yet, so this page is the
// last chance to fix the description before the eval set crystallizes.
//
// Confirm → continue to the workflow detail page. Revise → delete the
// row and bounce back to /new (the kernel cascades skills + traces +
// audit; same DELETE the Settings tab uses).
export default async function ReviewWorkflowPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let spec: WorkflowSpecShape | null = null
  let description = ''
  let skills: SkillSummary[] = []
  let evalCases: EvalCaseSummary[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    const [anatomy, skillList, evalList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowSkills(wfId),
      listWorkflowEvalCases(wfId),
    ])
    spec = anatomy.spec
    description = anatomy.description
    skills = skillList.items
    evalCases = evalList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
    apiError = {
      title: 'Could not load workflow.',
      detail: err instanceof Error ? err.message : String(err),
    }
  }

  if (apiError) {
    return (
      <div role="alert" className="api-banner">
        <strong>{apiError.title}</strong> {apiError.detail}
      </div>
    )
  }

  const continueHref = `/workspaces/${wsId}/workflows/${wfId}`

  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <h1 className="gen-title">Review · {wfId}</h1>
        <p className="gen-sub">
          ownEvo generated a workflow spec from your description. Look it
          over before the loop starts running — if anything is off, click
          Revise to delete this row and edit the description.
        </p>
      </header>

      <Steps step="review" />

      <section className="review-block">
        <h2 className="section-title">Your description</h2>
        <p className="review-description">{description}</p>
      </section>

      <section className="review-block">
        <AgentAnatomy
          wsId={wsId}
          workflowId={wfId}
          skills={skills}
          spec={spec}
        />
      </section>

      <section className="review-block">
        <h2 className="section-title">Eval cases</h2>
        {evalCases.length === 0 ? (
          <div className="review-eval-empty">
            <p>
              No eval cases generated yet. The improvement loop needs them
              to score iterations. Generate now (one LLM call,
              ~25-40s) or skip and run the first iteration to trigger
              generation lazily.
            </p>
            <GenerateEvalCasesButton wsId={wsId} wfId={wfId} hasExisting={false} />
          </div>
        ) : (
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {evalCases.length} eval case{evalCases.length === 1 ? '' : 's'}{' '}
            ready ({evalCases.filter((c) => c.is_test_fold).length} test
            fold,{' '}
            {evalCases.filter((c) => !c.is_test_fold).length} train fold).{' '}
            <Link
              href={`/workspaces/${wsId}/workflows/${wfId}/eval-cases`}
              style={{ color: 'var(--accent)' }}
            >
              Open eval cases →
            </Link>
          </p>
        )}
      </section>

      <div className="gen-action-row">
        <ReviseButton wsId={wsId} wfId={wfId} />
        <Link href={continueHref} className="btn btn-primary">
          Looks good · open workflow &rsaquo;
        </Link>
      </div>
    </div>
  )
}

// Inlined three-step indicator — same shape as the one on /new but
// scoped to this page so the review surface doesn't have to import
// from a sibling route's page component.
function Steps({ step }: { step: 'describe' | 'review' | 'baseline' }) {
  return (
    <div className="steps">
      <div className={`step done`}>
        <div className="step-num">✓</div>
        <div className="step-label">Describe</div>
      </div>
      <div className="step-connector" />
      <div className={`step ${step === 'review' ? 'active' : ''}`}>
        <div className="step-num">2</div>
        <div className="step-label">Review generated</div>
      </div>
      <div className="step-connector" />
      <div className={`step ${step === 'baseline' ? 'active' : ''}`}>
        <div className="step-num">3</div>
        <div className="step-label">Run baseline</div>
      </div>
    </div>
  )
}
