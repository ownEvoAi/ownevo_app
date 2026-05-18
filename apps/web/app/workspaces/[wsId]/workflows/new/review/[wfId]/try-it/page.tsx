import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  type EvalCaseSummary,
  getWorkflowAnatomy,
  KernelApiError,
  listWorkflowEvalCases,
  type WorkflowAnatomy,
} from '@/lib/api'
import { ConfirmButton } from '../confirm-button'
import { ReviseButton } from '../revise-button'
import { TryItForm } from './try-it-form'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

/**
 * Try-it tab on the new-workflow Step 2 review page (PLAN 8.5.2).
 *
 * Reuses the review-page chrome (preview-wrap, gen-head, Steps,
 * action row) and renders the TryItForm in the body. The kernel
 * already loads the spec / sim_plan / metric on the POST /try
 * endpoint; we only need eval cases here to populate the picker.
 */
export default async function TryItPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let anatomy: WorkflowAnatomy | null = null
  let cases: EvalCaseSummary[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    const [a, evalList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      listWorkflowEvalCases(wfId),
    ])
    anatomy = a
    cases = evalList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
    apiError = {
      title: 'Could not load workflow.',
      detail: err instanceof Error ? err.message : String(err),
    }
  }

  if (apiError || !anatomy) {
    return (
      <div role="alert" className="api-banner">
        <strong>{apiError?.title ?? 'Workflow unavailable.'}</strong>{' '}
        {apiError?.detail ?? ''}
      </div>
    )
  }

  const reviewHref = `/workspaces/${wsId}/workflows/new/review/${wfId}`
  const continueHref = `/workspaces/${wsId}/workflows/${wfId}`

  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <h1 className="gen-title">Try it · dry-run one case</h1>
        <p className="gen-sub">
          Pick a generated eval case and run the agent against it end-to-end.
          Output, trace, and cost appear inline. No iteration, proposal, or
          audit row is written — this is a sandboxed dry-run so you can see
          how the agent behaves before committing.
        </p>
      </header>

      <Steps step="review" />

      <ReviewTabs wsId={wsId} wfId={wfId} active="try-it" />

      <TryItForm wfId={wfId} cases={cases} />

      <div className="gen-action-row">
        <ReviseButton wsId={wsId} wfId={wfId} />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Link href={reviewHref} className="btn btn-secondary">
            ‹ Back to review
          </Link>
          <ConfirmButton continueHref={continueHref} />
        </div>
      </div>
    </div>
  )
}

function ReviewTabs({
  wsId,
  wfId,
  active,
}: {
  wsId: string
  wfId: string
  active: 'review' | 'try-it'
}) {
  return (
    <div className="tabs" style={{ marginBottom: 20 }}>
      <Link
        href={`/workspaces/${wsId}/workflows/new/review/${wfId}`}
        className={`tab${active === 'review' ? ' active' : ''}`}
      >
        Review generated
      </Link>
      <Link
        href={`/workspaces/${wsId}/workflows/new/review/${wfId}/try-it`}
        className={`tab${active === 'try-it' ? ' active' : ''}`}
      >
        Try it
      </Link>
    </div>
  )
}

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
