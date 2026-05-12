import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  getIterationDetail,
  kernelError,
  KernelApiError,
  type IterationCaseRow,
  type IterationDetail,
} from '@/lib/api'
import { formatDateTime, formatScore, relativeTime } from '@/lib/format'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string; idx: string }>
}

// PLAN row 8.4.8 — per-iteration drill-down. The lift chart on
// Overview now plots iteration_index x val_score; the case-by-case
// signal that drives improvement lives one level deeper. Click an
// iteration row and land here to see: which cases passed, which
// failed, what the agent predicted vs the ground truth, and which
// failure cluster anchored the next proposed instruction edit.
export default async function IterationDetailPage({ params }: PageProps) {
  const { wsId, wfId, idx } = await params
  const iterationIndex = Number.parseInt(idx, 10)
  if (!Number.isFinite(iterationIndex) || iterationIndex < 0) {
    notFound()
  }

  let detail: IterationDetail
  try {
    detail = await getIterationDetail(wfId, iterationIndex)
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
    const apiError = kernelError(err)
    return (
      <div role="alert" className="api-banner">
        <strong>{apiError.title}</strong> {apiError.detail}
      </div>
    )
  }

  const failedCases = detail.cases.filter((c) => c.passed === false)
  const passedCases = detail.cases.filter((c) => c.passed === true)
  const unknownCases = detail.cases.filter((c) => c.passed === null)

  return (
    <>
      <nav className="crumb-row" style={{ marginTop: -8 }}>
        <Link href={`/workspaces/${wsId}/workflows/${wfId}`}>Overview</Link>
        <span className="sep">/</span>
        <span>Iteration {detail.iteration_index}</span>
      </nav>

      <header className="page-header" style={{ marginBottom: 12 }}>
        <div>
          <h1 className="page-title">Iteration {detail.iteration_index}</h1>
          <p className="page-subtitle">
            {detail.state} ·{' '}
            {detail.val_score !== null
              ? `val_score ${formatScore(detail.val_score)}`
              : 'val_score —'}
            {' · '}
            {detail.n_failed}/{detail.n_cases} failed
            {detail.ended_at !== null ? (
              <>
                {' · '}
                <span title={formatDateTime(detail.ended_at)}>
                  {relativeTime(detail.ended_at)}
                </span>
              </>
            ) : null}
          </p>
        </div>
        <div className="page-actions" style={{ gap: 8 }}>
          {detail.proposal_id ? (
            <Link
              href={`/workspaces/${wsId}/proposals/${detail.proposal_id}`}
              className="btn btn-primary"
              style={{ fontSize: 12, padding: '6px 12px' }}
            >
              View proposal →
            </Link>
          ) : null}
          {detail.cluster_id ? (
            <Link
              href={`/workspaces/${wsId}/workflows/${wfId}/failures`}
              className="btn btn-secondary"
              style={{ fontSize: 12, padding: '6px 12px' }}
            >
              View failures →
            </Link>
          ) : null}
        </div>
      </header>

      <section className="iteration-meta">
        <Meta
          label="Best ever before"
          value={
            detail.best_ever_score_before !== null
              ? formatScore(detail.best_ever_score_before)
              : '—'
          }
        />
        <Meta
          label="Best ever after"
          value={
            detail.best_ever_score_after !== null
              ? formatScore(detail.best_ever_score_after)
              : '—'
          }
        />
        <Meta
          label="Dominant cluster"
          value={detail.cluster_label ?? '— (no clusters)'}
        />
        <Meta
          label="Cases"
          value={`${detail.n_passed} passed · ${detail.n_failed} failed`}
        />
        <Meta
          label="Started"
          value={formatDateTime(detail.started_at)}
        />
        <Meta
          label="Ended"
          value={
            detail.ended_at !== null ? formatDateTime(detail.ended_at) : '—'
          }
        />
      </section>

      {failedCases.length > 0 && (
        <CaseSection
          label="Failed"
          tone="fail"
          wsId={wsId}
          cases={failedCases}
        />
      )}
      {unknownCases.length > 0 && (
        <CaseSection
          label="Unknown"
          tone="unknown"
          wsId={wsId}
          cases={unknownCases}
        />
      )}
      {passedCases.length > 0 && (
        <CaseSection
          label="Passed"
          tone="pass"
          wsId={wsId}
          cases={passedCases}
        />
      )}

      {detail.cases.length === 0 && (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 28,
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: 13,
          }}
        >
          No per-case traces recorded for this iteration. (Older
          iterations from before the iteration_runner wrote traces will
          show empty here.)
        </div>
      )}
    </>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="iteration-meta-cell">
      <div className="iteration-meta-label">{label}</div>
      <div className="iteration-meta-value">{value}</div>
    </div>
  )
}

function CaseSection({
  label,
  tone,
  wsId,
  cases,
}: {
  label: string
  tone: 'fail' | 'pass' | 'unknown'
  wsId: string
  cases: IterationCaseRow[]
}) {
  return (
    <section style={{ marginTop: 18 }}>
      <h2 className="section-title" style={{ marginBottom: 8 }}>
        {label} · {cases.length}
      </h2>
      <div className={`iter-case-list iter-case-list-${tone}`}>
        <div className="iter-case-row iter-case-head">
          <span>Case</span>
          <span>Predicted</span>
          <span>Expected</span>
          <span>Fold</span>
          <span>Trace</span>
        </div>
        {cases.map((c) => (
          <Link
            key={c.trace_id}
            href={`/workspaces/${wsId}/traces/${c.trace_id}`}
            className="iter-case-row"
          >
            <span className="iter-case-id" title={c.case_id}>
              {c.case_id}
            </span>
            <span className={`iter-case-bool ${boolClass(c.predicted)}`}>
              {boolLabel(c.predicted)}
            </span>
            <span className={`iter-case-bool ${boolClass(c.expected)}`}>
              {boolLabel(c.expected)}
            </span>
            <span className="iter-case-fold">
              {c.is_test_fold ? 'test' : 'train'}
            </span>
            <span className="iter-case-trace">{c.trace_id.slice(0, 8)} ›</span>
          </Link>
        ))}
      </div>
    </section>
  )
}

function boolLabel(v: boolean | null): string {
  if (v === null) return '—'
  return v ? 'true' : 'false'
}

function boolClass(v: boolean | null): string {
  if (v === null) return ''
  return v ? 'is-true' : 'is-false'
}
