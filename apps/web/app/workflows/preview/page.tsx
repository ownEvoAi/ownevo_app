// W5.5 — `/workflows/preview` route. Renders the "Review what we'll
// build" surface (mock-04) populated from `GET /api/nl-gen/preview`.
// Coverage badge is the headliner; the four artifact sections render
// read-only (no Edit / Regenerate buttons wired). Workflow picker at
// the top swaps fixtures via the `?workflow_id=` query param.
//
// When the live `POST /api/nl-gen/generate` flow lands (W6), this
// route shape is the target — we'll add a write path then.

import Link from 'next/link'
import { redirect } from 'next/navigation'
import {
  getPreview,
  KernelApiError,
  listPreviewWorkflows,
  type PreviewIndexEntry,
  type PreviewResponse,
} from '@/lib/api'
import { MetaEvalCoverageBadge } from './coverage-badge'

interface PageProps {
  searchParams: Promise<{ workflow_id?: string }>
}

export default async function WorkflowPreviewPage({ searchParams }: PageProps) {
  const { workflow_id: requested } = await searchParams

  let index
  try {
    index = await listPreviewWorkflows()
  } catch (err) {
    return <ApiErrorState err={err} />
  }

  if (index.items.length === 0) {
    return <EmptyIndexState />
  }

  const fallbackId = index.items[0].workflow_id
  const workflowId = requested || fallbackId

  // If the requested id isn't in the index, redirect to the fallback
  // rather than serve a 404 — the picker is the obvious recovery path.
  if (!index.items.some((it) => it.workflow_id === workflowId)) {
    redirect(`/workflows/preview?workflow_id=${encodeURIComponent(fallbackId)}`)
  }

  let preview: PreviewResponse
  try {
    preview = await getPreview(workflowId)
  } catch (err) {
    return <ApiErrorState err={err} />
  }

  return (
    <div className="preview-wrap">
      <div className="preview-banner-strip">
        <strong>PREVIEW</strong> · demo data from kernel fixtures ·
        <code> provenance={preview.provenance}</code>
      </div>

      <Steps />

      <header className="gen-head">
        <h1 className="gen-title">Review what we&rsquo;ll build</h1>
        <p className="gen-sub">
          From your description, ownEvo generated a simulator, an eval set, and
          a success metric. Each is editable. Once you approve, the agent runs
          against the simulator to establish a baseline &mdash; then the
          improvement loop starts.
        </p>
      </header>

      <WorkflowPicker
        items={index.items}
        active={preview.workflow_id}
      />

      <div className="source-quote">
        <span className="source-quote-label">
          From your<br />description
        </span>
        <p className="source-quote-body">{preview.description}</p>
      </div>

      <MetaEvalCoverageBadge judgment={preview.meta_eval_judgment} />

      <SimulatorSection plan={preview.simulation_plan} />
      <EvalCasesSection caseSet={preview.eval_case_set} />
      <SuccessMetricSection metric={preview.metric_definition} />

      <div className="gen-action-row">
        <Link href="/inbox" className="btn btn-secondary">
          &lsaquo; Back
        </Link>
        <button
          type="button"
          className="btn btn-primary"
          disabled
          title="Run-baseline wire-up lands in W6 (POST /api/nl-gen/generate)"
        >
          Run baseline &rsaquo;
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page chrome
// ---------------------------------------------------------------------------

function Steps() {
  return (
    <div className="steps">
      <div className="step done">
        <div className="step-num">✓</div>
        <div className="step-label">Describe</div>
      </div>
      <div className="step-connector" />
      <div className="step active">
        <div className="step-num">2</div>
        <div className="step-label">Review generated</div>
      </div>
      <div className="step-connector" />
      <div className="step">
        <div className="step-num">3</div>
        <div className="step-label">Run baseline</div>
      </div>
    </div>
  )
}

function WorkflowPicker({
  items,
  active,
}: {
  items: PreviewIndexEntry[]
  active: string
}) {
  if (items.length <= 1) return null
  return (
    <div className="workflow-picker">
      <span className="workflow-picker-label">Demo workflow:</span>
      {items.map((it) => (
        <Link
          key={it.workflow_id}
          href={`/workflows/preview?workflow_id=${encodeURIComponent(it.workflow_id)}`}
          className={`filter-chip ${it.workflow_id === active ? 'active' : ''}`}
        >
          {it.workflow_id}
        </Link>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Artifact section helpers — read structure off the spec/plan/case-set/metric
// dicts directly. Pydantic serialization of these schemas is stable, so
// indexing into known fields is safe.
// ---------------------------------------------------------------------------

function asString(v: unknown): string {
  return typeof v === 'string' ? v : JSON.stringify(v)
}

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

function SimulatorSection({ plan }: { plan: Record<string, unknown> }) {
  const description = asString(plan.description ?? '')
  const nSteps = plan.n_steps_default ?? 0
  const eventFields = asArray(plan.event_fields)

  return (
    <section className="gen-section">
      <SectionHead
        title="Simulator"
        meta={`${eventFields.length} event field(s) · ${String(nSteps)} steps default`}
        glyph={<SimGlyph />}
      />
      <p className="artifact-desc" style={{ marginBottom: 12 }}>
        {description}
      </p>
      {eventFields.length > 0 && (
        <div className="artifact-list">
          {eventFields.map((field, idx) => {
            const f = field as Record<string, unknown>
            return (
              <div key={idx} className="artifact">
                <div className="artifact-icon">
                  <FieldGlyph />
                </div>
                <div className="artifact-body">
                  <div className="artifact-title">
                    <code>{asString(f.name)}</code>
                    <span className="artifact-type"> · {asString(f.type)}</span>
                  </div>
                  {typeof f.description === 'string' && (
                    <div className="artifact-desc">{f.description}</div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function EvalCasesSection({ caseSet }: { caseSet: Record<string, unknown> }) {
  const cases = asArray(caseSet.cases)
  const trainCount = cases.filter(
    (c) => !(c as Record<string, unknown>).is_test_fold,
  ).length
  const testCount = cases.length - trainCount

  return (
    <section className="gen-section">
      <SectionHead
        title={`Eval cases · ${cases.length} generated`}
        meta={`${trainCount} train · ${testCount} test`}
        glyph={<EvalGlyph />}
      />
      <div className="eval-table">
        <div className="eval-row head">
          <div>#</div>
          <div>Case</div>
          <div>Expected</div>
          <div>Fold</div>
        </div>
        {cases.map((raw, i) => {
          const c = raw as Record<string, unknown>
          return (
            <div key={i} className="eval-row">
              <div className="eval-num">{i + 1}</div>
              <div>
                <div className="eval-name">{asString(c.case_id ?? `case-${i}`)}</div>
                {typeof c.rationale === 'string' && (
                  <div className="eval-source">{c.rationale}</div>
                )}
              </div>
              <div>
                <span
                  className={`pill ${
                    c.expected_value === true
                      ? 'green'
                      : c.expected_value === false
                        ? 'red'
                        : 'outline'
                  }`}
                >
                  {String(c.expected_value ?? '—')}
                </span>
              </div>
              <div>
                <span className={`pill ${c.is_test_fold ? 'amber' : 'outline'}`}>
                  {c.is_test_fold ? 'test' : 'train'}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function SuccessMetricSection({
  metric,
}: {
  metric: Record<string, unknown>
}) {
  const family = asString(metric.family ?? '')
  const direction = asString(metric.direction ?? '')
  const target = metric.target_value ?? '—'
  const rationale = asString(metric.rationale ?? '')

  return (
    <section className="gen-section">
      <SectionHead
        title="Success metric"
        meta={`${family} · ${direction} is better · target ${String(target)}`}
        glyph={<MetricGlyph />}
      />
      <div className="metric-def">
        <div>
          <span className="key">family:</span> {family}
        </div>
        <div>
          <span className="key">direction:</span> {direction}
        </div>
        <div>
          <span className="key">target_value:</span>{' '}
          <span className="num">{String(target)}</span>
        </div>
        <div className="comment"># {rationale}</div>
      </div>
    </section>
  )
}

function SectionHead({
  title,
  meta,
  glyph,
}: {
  title: string
  meta: string
  glyph: React.ReactNode
}) {
  return (
    <div className="gen-section-head">
      <div className="gen-section-title-row">
        <div className="gen-section-icon">{glyph}</div>
        <div>
          <div className="gen-section-title">{title}</div>
          <div className="gen-section-meta">{meta}</div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Glyphs
// ---------------------------------------------------------------------------

function SimGlyph() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden>
      <circle cx="9" cy="9" r="6" />
      <path d="M9 5 L9 9 L12 11" />
    </svg>
  )
}

function EvalGlyph() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden>
      <path d="M3 4 L15 4 L15 14 L3 14 Z M3 8 L15 8 M9 8 L9 14" />
    </svg>
  )
}

function MetricGlyph() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden>
      <path d="M3 13 L7 7 L11 11 L15 4" />
    </svg>
  )
}

function FieldGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M3 8 L13 8 M9 4 L13 8 L9 12" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Error / empty states
// ---------------------------------------------------------------------------

function ApiErrorState({ err }: { err: unknown }) {
  const msg =
    err instanceof KernelApiError
      ? `${err.status}: ${err.detail}`
      : err instanceof Error
        ? err.message
        : String(err)
  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <h1 className="gen-title">Preview unavailable</h1>
        <p className="gen-sub">
          The kernel preview API didn&rsquo;t answer. Is{' '}
          <code>uvicorn ownevo_kernel.api.app:app</code> running on port 8000?
        </p>
      </header>
      <pre style={{ color: 'var(--red)', whiteSpace: 'pre-wrap' }}>{msg}</pre>
    </div>
  )
}

function EmptyIndexState() {
  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <h1 className="gen-title">No preview workflows</h1>
        <p className="gen-sub">
          The kernel has no NL-gen fixtures registered. This shouldn&rsquo;t
          happen in a fresh checkout &mdash; check{' '}
          <code>ownevo_kernel.nl_gen.fixtures</code>.
        </p>
      </header>
    </div>
  )
}
