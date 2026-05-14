import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  getIterationDetail,
  getWorkflowAnatomy,
  getWorkflowIterations,
  kernelError,
  KernelApiError,
  type IterationDetail,
} from '@/lib/api'
import { formatScore, workflowDisplayTitle } from '@/lib/format'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// "First run complete — here's what changed." Landing after iteration
// #0 finishes so the operator sees a starting metric, the per-case
// roster, and a clear next step — instead of being dumped onto the
// Overview tab whose "improvement loop active" card buries the new
// signal.
//
// If the workflow has more than one iteration, this page redirects
// back to Overview — the baseline-complete framing only makes sense
// the first time. We do that with a NextResponse.redirect on the
// Overview link target rather than at the server-component level
// because middleware would require app-wide routing knowledge for
// a single screen.
export default async function BaselineCompletePage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let detail: IterationDetail | null = null
  let workflowTitle = wfId
  let apiError: { title: string; detail: string } | null = null
  let iterationCount = 0
  try {
    const [anatomy, iterList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowIterations(wfId),
    ])
    workflowTitle = workflowDisplayTitle(anatomy.id, anatomy.description, 80)
    iterationCount = iterList.items.length
    if (iterationCount === 0) {
      notFound()
    }
    detail = await getIterationDetail(wfId, 0)
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
    apiError = kernelError(err)
  }

  if (apiError || !detail) {
    return (
      <div role="alert" className="api-banner">
        <strong>{apiError?.title ?? 'Could not load baseline.'}</strong>{' '}
        {apiError?.detail ?? ''}
      </div>
    )
  }

  const durationSec =
    detail.ended_at !== null
      ? Math.round(
          (new Date(detail.ended_at).getTime() -
            new Date(detail.started_at).getTime()) /
            1000,
        )
      : null
  const durationLabel =
    durationSec === null
      ? 'in flight'
      : durationSec < 60
        ? `${durationSec}s`
        : `${Math.floor(durationSec / 60)}m ${durationSec % 60}s`

  const passedPct = detail.n_cases > 0 ? (detail.n_passed / detail.n_cases) * 100 : 0
  const failedCases = detail.cases.filter((c) => c.passed === false)
  const previewCases = detail.cases.slice(0, 8)
  const overviewHref = `/workspaces/${wsId}/workflows/${wfId}`

  return (
    <div className="preview-wrap">
      <Steps step="baseline" />

      <section className="baseline-hero">
        <div className="baseline-hero-check" aria-hidden>
          <svg viewBox="0 0 28 28">
            <path d="M7 14 L12 19 L21 9" />
          </svg>
        </div>
        <h1 className="baseline-hero-title">Baseline complete</h1>
        <p className="baseline-hero-sub">
          The agent ran iteration #0 against all {detail.n_cases} eval
          case{detail.n_cases === 1 ? '' : 's'} for{' '}
          <strong>{workflowTitle}</strong>. You now have a starting
          val_score; the improvement loop is ready.
        </p>
      </section>

      <div className="baseline-metrics">
        <Metric
          label="val_score"
          value={detail.val_score !== null ? formatScore(detail.val_score) : '—'}
          sub="baseline · day 0"
        />
        <Metric
          label="Cases passed"
          value={`${detail.n_passed} / ${detail.n_cases}`}
          sub={
            detail.n_failed > 0
              ? `${detail.n_failed} failed · ${passedPct.toFixed(0)}% pass rate`
              : 'all green at baseline'
          }
        />
        <Metric label="Run time" value={durationLabel} sub="wall-clock" />
        <Metric
          label="Next step"
          value={detail.proposal_id ? 'Proposal ready' : 'Run again'}
          sub={
            detail.proposal_id
              ? 'gated by the regression suite'
              : 'proposer found no improvement vector'
          }
        />
      </div>

      <section className="baseline-block">
        <div className="baseline-block-head">
          <h2 className="section-title">Per-case results</h2>
          <span className="run-meta">
            showing {previewCases.length} of {detail.cases.length} ·{' '}
            <Link
              href={`/workspaces/${wsId}/workflows/${wfId}/iterations/0`}
              style={{ color: 'var(--accent)' }}
            >
              full roster →
            </Link>
          </span>
        </div>
        <div className="baseline-case-list">
          {previewCases.length === 0 ? (
            <div className="baseline-case-empty">
              No per-case traces recorded — older iterations from before
              the iteration_runner wrote traces will show empty here.
            </div>
          ) : (
            previewCases.map((c) => (
              <Link
                key={c.trace_id}
                href={`/workspaces/${wsId}/traces/${c.trace_id}`}
                className="baseline-case-row"
              >
                <span
                  className={`baseline-case-dot ${
                    c.passed === true
                      ? 'pass'
                      : c.passed === false
                        ? 'fail'
                        : 'unknown'
                  }`}
                  aria-hidden
                />
                <span className="baseline-case-id" title={c.case_id}>
                  {c.case_id}
                </span>
                <span className="baseline-case-fold">
                  {c.is_test_fold ? 'test' : 'train'}
                </span>
                <span
                  className={`baseline-case-status ${
                    c.passed === true
                      ? 'pass'
                      : c.passed === false
                        ? 'fail'
                        : 'unknown'
                  }`}
                >
                  {c.passed === true
                    ? 'passed'
                    : c.passed === false
                      ? 'failed'
                      : '—'}
                </span>
              </Link>
            ))
          )}
        </div>
      </section>

      <section className="baseline-block">
        <h2 className="section-title">Your starting point</h2>
        <div className="lift-preview">
          <div className="lift-pre-head">
            <div>
              <div className="lift-pre-title">val_score baseline</div>
              <div className="lift-pre-current">
                <span className="lift-pre-val">
                  {detail.val_score !== null
                    ? (detail.val_score * 100).toFixed(1)
                    : '—'}
                </span>
                {detail.val_score !== null && (
                  <span className="lift-pre-unit">%</span>
                )}
              </div>
            </div>
            <span className="pill outline">Baseline · iter 0</span>
          </div>
          <svg
            viewBox="0 0 720 80"
            style={{ width: '100%', height: 80 }}
            aria-label="Baseline starting point"
          >
            <line
              x1="60"
              y1="50"
              x2="700"
              y2="50"
              stroke="var(--text-faint)"
              strokeDasharray="3 3"
              strokeWidth="1"
            />
            <text
              x="65"
              y="46"
              fill="var(--text-muted)"
              fontSize="10"
            >
              improvements climb above this line
            </text>
            <circle
              cx="60"
              cy="50"
              r="5"
              fill="var(--accent)"
              stroke="var(--bg)"
              strokeWidth="2"
            />
            <text
              x="60"
              y="72"
              textAnchor="middle"
              fill="var(--text-muted)"
              fontSize="10"
            >
              today
            </text>
          </svg>
          <div className="lift-pre-meta">
            As iterations run, failure clusters surface and proposals
            queue up. Each approved improvement is gated against the
            same {detail.n_cases}-case suite plus any new cases promoted
            from clusters. The lift chart climbs from here.
          </div>
        </div>
      </section>

      <div className="final-row">
        <div className="final-row-note">
          {failedCases.length > 0 ? (
            <>
              {failedCases.length} eval case
              {failedCases.length === 1 ? '' : 's'} failed at baseline —
              the loop will cluster those first.
            </>
          ) : (
            <>
              All eval cases passed at baseline. The loop will still
              cluster any production traces that fail to lift this number
              further.
            </>
          )}
        </div>
        <div className="final-row-actions">
          <Link
            href={`/workspaces/${wsId}/workflows/${wfId}/iterations/0`}
            className="btn btn-secondary"
          >
            See the run in detail
          </Link>
          <Link href={overviewHref} className="btn btn-primary">
            Continue to dashboard &rsaquo;
          </Link>
        </div>
      </div>
    </div>
  )
}

function Metric({
  label,
  value,
  sub,
}: {
  label: string
  value: string
  sub: string
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-delta flat">{sub}</div>
    </div>
  )
}

function Steps({ step }: { step: 'describe' | 'review' | 'baseline' }) {
  return (
    <div className="steps">
      <div className="step done">
        <div className="step-num">✓</div>
        <div className="step-label">Describe</div>
      </div>
      <div className="step-connector" />
      <div className="step done">
        <div className="step-num">✓</div>
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
