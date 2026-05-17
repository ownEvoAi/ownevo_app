import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  type AgentToolSpec,
  type DataSourceSpec,
  type EnvGeneratorSpec,
  type EvalCaseSummary,
  getWorkflowAnatomy,
  getWorkflowSkills,
  KernelApiError,
  listWorkflowEvalCases,
  type MetricDefinitionShape,
  type PersonaSpec,
  type SkillSummary,
  type SpecProvenance,
  type WorkflowAnatomy,
} from '@/lib/api'
import { GenerateEvalCasesButton } from '../../../[wfId]/eval-cases/generate-button'
import { ReviseButton } from './revise-button'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Step 2 of the new-workflow flow — review what NL-gen produced before
// the loop starts spending tokens against it. The kernel committed the
// row at the end of step 1 (spec + sim_plan + metric_definition are in
// DB), but eval cases haven't been generated yet (or were generated
// lazily by the iteration runner). This is the last chance to fix the
// description before the eval set crystallizes.
//
// Confirm → continue to the workflow detail page. Revise → delete the
// row and bounce back to /new (the kernel cascades skills + traces +
// audit; same DELETE the Settings tab uses).
//
// PLAN 8.4.11 — parity with mock `04-new-workflow-step2.html`. Four
// gen-section blocks plus the source-quote header carry the
// per-tool / per-persona / per-environment / metric provenance
// captions ("derived from <user phrase>" or "inferred from <pattern>")
// that prove NL-gen actually understood the description.
export default async function ReviewWorkflowPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let anatomy: WorkflowAnatomy | null = null
  let skills: SkillSummary[] = []
  let evalCases: EvalCaseSummary[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    const [anatomyRes, skillList, evalList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowSkills(wfId),
      listWorkflowEvalCases(wfId),
    ])
    anatomy = anatomyRes
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

  if (apiError || !anatomy) {
    return (
      <div role="alert" className="api-banner">
        <strong>{apiError?.title ?? 'Workflow unavailable.'}</strong>{' '}
        {apiError?.detail ?? ''}
      </div>
    )
  }

  const continueHref = `/workspaces/${wsId}/workflows/${wfId}`
  const operateHref = `/workspaces/${wsId}/workflows/${wfId}/operate`
  const description = anatomy.description
  const spec = anatomy.spec
  const tools = spec.tools ?? []
  const personas = spec.environment?.personas ?? []
  const envGenerators = spec.environment?.env_generators ?? []
  const dataSources = spec.environment?.data_sources ?? []
  const primitives = spec.ui?.tabs?.[0]?.primitives ?? []
  const metricDef = anatomy.metric_definition ?? null
  const simPlan = anatomy.simulation_plan ?? null

  // Aux skill count used in the meta line under the page header.
  const skillCount = skills.length
  const simMeta = simPlan
    ? `${tools.length} tools · ${personas.length} personas · ${envGenerators.length + dataSources.length} environment sources`
    : 'simulator not generated yet'

  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <h1 className="gen-title">Review what we&apos;ll build</h1>
        <p className="gen-sub">
          ownEvo generated the workflow spec, simulator, success metric,
          and (when available) the eval cases from your description.
          Look it over before the loop starts running. If anything is
          off, Revise deletes this row and lets you edit the
          description. Confirming opens the workflow and the next
          iteration writes against this spec.
        </p>
      </header>

      <Steps step="review" />

      {description ? (
        <section className="source-quote">
          <div className="source-quote-label">From you</div>
          <p className="source-quote-body">&ldquo;{description}&rdquo;</p>
        </section>
      ) : null}

      <SimulatorSection
        meta={simMeta}
        tools={tools}
        personas={personas}
        envGenerators={envGenerators}
        dataSources={dataSources}
      />

      <EvalCasesSection cases={evalCases} wsId={wsId} wfId={wfId} />

      <MetricSection metric={metricDef} />

      <PrimitivesSection
        primitives={primitives}
        operateHref={operateHref}
        skillCount={skillCount}
      />

      <div className="gen-action-row">
        <ReviseButton wsId={wsId} wfId={wfId} />
        <Link href={continueHref} className="btn btn-primary">
          Looks good · open workflow &rsaquo;
        </Link>
      </div>
    </div>
  )
}

// ─── Source-provenance badge ──────────────────────────────────────
// Renders `derived from "<phrase>"` or `inferred from <pattern>`
// under a tool / persona / generator / metric. Returns null when the
// kernel didn't attach provenance (legacy rows; hand-authored items).
function ProvenanceLine({ provenance }: { provenance?: SpecProvenance | null }) {
  if (!provenance) return null
  const isDerived = provenance.kind === 'derived'
  return (
    <div className="artifact-derived">
      {isDerived ? (
        <>derived from <em>&ldquo;{provenance.source}&rdquo;</em></>
      ) : (
        <>inferred from <em>{provenance.source}</em></>
      )}
    </div>
  )
}

// ─── Section 1: Simulator ────────────────────────────────────────
function SimulatorSection(props: {
  meta: string
  tools: AgentToolSpec[]
  personas: PersonaSpec[]
  envGenerators: EnvGeneratorSpec[]
  dataSources: DataSourceSpec[]
}) {
  const { meta, tools, personas, envGenerators, dataSources } = props
  const empty =
    tools.length === 0 &&
    personas.length === 0 &&
    envGenerators.length === 0 &&
    dataSources.length === 0
  return (
    <SectionShell title="Simulator" meta={meta}>
      {empty ? (
        <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          The simulator hasn&apos;t been generated yet — Revise to edit
          the description, or continue and the iteration runner will
          generate it lazily.
        </p>
      ) : (
        <>
          {tools.length > 0 ? (
            <>
              <div className="sub-block-label">Tools the agent can call</div>
              <div className="artifact-list" style={{ marginBottom: 6 }}>
                {tools.map((t) => (
                  <div key={t.name} className="artifact">
                    <div className="artifact-icon">›</div>
                    <div className="artifact-body">
                      <div className="artifact-title">
                        <code>
                          {t.name}(
                          {(t.inputs ?? [])
                            .map((p) => p.name)
                            .join(', ')}
                          )
                        </code>
                      </div>
                      {t.description ? (
                        <div className="artifact-desc">{t.description}</div>
                      ) : null}
                      <ProvenanceLine provenance={t.provenance} />
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}

          {personas.length > 0 ? (
            <>
              <div className="sub-block-label">User behaviour (simulated)</div>
              <div className="artifact-list" style={{ marginBottom: 6 }}>
                {personas.map((p, i) => (
                  <div key={`${p.role}-${i}`} className="artifact">
                    <div className="artifact-icon">◉</div>
                    <div className="artifact-body">
                      <div className="artifact-title">
                        {p.role}
                        {p.cadence ? (
                          <span className="artifact-type">
                            {' '}
                            · {p.cadence}
                          </span>
                        ) : null}
                      </div>
                      {p.description ? (
                        <div className="artifact-desc">{p.description}</div>
                      ) : null}
                      <ProvenanceLine provenance={p.provenance} />
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}

          {envGenerators.length > 0 || dataSources.length > 0 ? (
            <>
              <div className="sub-block-label">Environment generators</div>
              <div className="artifact-list">
                {envGenerators.map((g) => (
                  <div key={g.name} className="artifact">
                    <div className="artifact-icon">◇</div>
                    <div className="artifact-body">
                      <div className="artifact-title">{g.name}</div>
                      {g.description ? (
                        <div className="artifact-desc">{g.description}</div>
                      ) : null}
                      <ProvenanceLine provenance={g.provenance} />
                    </div>
                  </div>
                ))}
                {dataSources.map((d) => (
                  <div key={d.id} className="artifact">
                    <div className="artifact-icon">▤</div>
                    <div className="artifact-body">
                      <div className="artifact-title">
                        {d.id}
                        {d.entity ? (
                          <span className="artifact-type">
                            {' '}
                            · {d.entity}
                          </span>
                        ) : null}
                      </div>
                      {d.description ? (
                        <div className="artifact-desc">{d.description}</div>
                      ) : null}
                      <ProvenanceLine provenance={d.provenance} />
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </>
      )}
    </SectionShell>
  )
}

// ─── Section 2: Eval cases ───────────────────────────────────────
function EvalCasesSection({
  cases,
  wsId,
  wfId,
}: {
  cases: EvalCaseSummary[]
  wsId: string
  wfId: string
}) {
  const total = cases.length
  if (total === 0) {
    return (
      <SectionShell title="Eval cases" meta="0 generated">
        <div className="review-eval-empty">
          <p>
            No eval cases generated yet. The improvement loop needs them
            to score iterations. Generate now (one LLM call, ~25-40s) or
            skip and run the first iteration to trigger generation
            lazily.
          </p>
          <GenerateEvalCasesButton wsId={wsId} wfId={wfId} hasExisting={false} />
        </div>
      </SectionShell>
    )
  }

  const pastMisses = cases.filter((c) => c.category === 'past-miss').length
  const inferred = cases.filter((c) => c.category === 'inferred').length
  const other = total - pastMisses - inferred
  const metaParts: string[] = []
  if (pastMisses) metaParts.push(`${pastMisses} from past misses`)
  if (inferred) metaParts.push(`${inferred} from domain patterns`)
  if (other) metaParts.push(`${other} hand-authored`)
  const meta = metaParts.join(' · ')

  const SHOW_LIMIT = 8
  const shown = cases.slice(0, SHOW_LIMIT)
  const remaining = total - shown.length

  return (
    <SectionShell title={`Eval cases · ${total} generated`} meta={meta}>
      <div className="eval-table">
        <div className="eval-row head">
          <div>#</div>
          <div>Case</div>
          <div>Type</div>
          <div>Fold</div>
          <div></div>
        </div>
        {shown.map((c, i) => (
          <div key={c.id} className="eval-row">
            <div className="eval-num">{i + 1}</div>
            <div>
              <div className="eval-name">{c.case_id}</div>
              {c.expected_behavior_provenance ? (
                <div className="eval-source">
                  {c.expected_behavior_provenance.kind === 'derived' ? (
                    <>
                      From: <em>&ldquo;{c.expected_behavior_provenance.source}&rdquo;</em>
                    </>
                  ) : (
                    <>
                      Pattern: <em>{c.expected_behavior_provenance.source}</em>
                    </>
                  )}
                </div>
              ) : c.rationale ? (
                <div className="eval-source">{c.rationale}</div>
              ) : null}
            </div>
            <div>
              <CategoryPill category={c.category} />
            </div>
            <div>
              <span className={`pill ${c.is_test_fold ? 'accent' : 'outline'}`}>
                {c.is_test_fold ? 'test' : 'train'}
              </span>
            </div>
            <div></div>
          </div>
        ))}
        {remaining > 0 ? (
          <div className="eval-row">
            <div className="eval-num">…</div>
            <div>
              <div className="eval-name">+ {remaining} more</div>
              <div className="eval-source">
                <Link
                  href={`/workspaces/${wsId}/workflows/${wfId}/eval-cases`}
                  style={{ color: 'var(--accent)' }}
                >
                  Open eval cases →
                </Link>
              </div>
            </div>
            <div></div>
            <div></div>
            <div></div>
          </div>
        ) : null}
      </div>
    </SectionShell>
  )
}

function CategoryPill({ category }: { category: string | null }) {
  if (category === 'past-miss') {
    return <span className="pill amber">Past miss</span>
  }
  if (category === 'inferred') {
    return <span className="pill outline">Inferred</span>
  }
  return <span className="pill outline">Manual</span>
}

// ─── Section 3: Success metric ───────────────────────────────────
function MetricSection({ metric }: { metric: MetricDefinitionShape | null }) {
  if (!metric) {
    return (
      <SectionShell title="Success metric" meta="not generated yet">
        <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          The success metric is generated alongside eval cases. Run the
          first iteration to trigger generation lazily, or Revise to
          start over.
        </p>
      </SectionShell>
    )
  }
  const meta = [metric.family, metric.direction].filter(Boolean).join(' · ')
  return (
    <SectionShell title="Success metric" meta={meta}>
      <div className="metric-def">
        <div>
          <span className="key">metric:</span> {metric.name ?? '(unnamed)'}
        </div>
        {metric.description ? (
          <div style={{ marginTop: 4, color: 'var(--text-3)' }}>
            {metric.description}
          </div>
        ) : null}
        {metric.rationale ? (
          <div style={{ marginTop: 4, color: 'var(--text-3)' }}>
            <span className="key">rationale:</span> {metric.rationale}
          </div>
        ) : null}
        {metric.provenance ? (
          <div className="comment">
            {metric.provenance.kind === 'derived'
              ? `# derived from your description: "${metric.provenance.source}"`
              : `# inferred from pattern: ${metric.provenance.source}`}
          </div>
        ) : null}
      </div>
    </SectionShell>
  )
}

// ─── Section 4: Operate-view UI primitives ───────────────────────
function PrimitivesSection({
  primitives,
  operateHref,
  skillCount,
}: {
  primitives: unknown[]
  operateHref: string
  skillCount: number
}) {
  const items = primitives
    .map((p) => {
      if (typeof p !== 'object' || p === null) return null
      const t = (p as { type?: unknown }).type
      return typeof t === 'string' ? t : null
    })
    .filter((s): s is string => s !== null)
  const meta =
    items.length > 0
      ? `${items.length} primitive${items.length === 1 ? '' : 's'} selected · ${skillCount} skill${skillCount === 1 ? '' : 's'} registered`
      : 'no operate-view primitives yet'
  return (
    <SectionShell
      title="Operate-view UI"
      meta={meta}
      action={
        <Link href={operateHref} className="btn btn-secondary" style={{ fontSize: 12 }}>
          Preview layout →
        </Link>
      }
    >
      {items.length === 0 ? (
        <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          NL-gen didn&apos;t pick any UI primitives for this workflow.
          The Operate tab will render an empty state until a primitive
          is added to the spec.
        </p>
      ) : (
        <div className="prim-grid">
          {items.map((t, i) => (
            <div key={`${t}-${i}`} className="prim selected">
              <div className="prim-icon">{t.slice(0, 1)}</div>
              <span className="prim-name">{t}</span>
              <span className="prim-check">✓</span>
            </div>
          ))}
        </div>
      )}
    </SectionShell>
  )
}

// ─── Shared section shell ────────────────────────────────────────
function SectionShell({
  title,
  meta,
  action,
  children,
}: {
  title: string
  meta?: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="gen-section">
      <div className="gen-section-head">
        <div className="gen-section-title-row">
          <div className="gen-section-icon">●</div>
          <div>
            <div className="gen-section-title">{title}</div>
            {meta ? <div className="gen-section-meta">{meta}</div> : null}
          </div>
        </div>
        {action ? <div className="gen-section-actions">{action}</div> : null}
      </div>
      {children}
    </section>
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
