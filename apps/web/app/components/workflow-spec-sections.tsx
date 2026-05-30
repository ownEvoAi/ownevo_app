import Link from 'next/link'
import type {
 AgentToolSpec,
 DataSourceSpec,
 DesignDimension,
 EnvGeneratorSpec,
 EvalCaseSummary,
 MetricDefinitionShape,
 PersonaSpec,
 SpecProvenance,
} from '@/lib/api'

// Section components reused by the authoring-time review page
// (`/workflows/new/review/[wfId]`) and the post-creation Spec tab
// (`/workflows/[wfId]/spec`). Pure renderers — the consuming page owns
// the data fetch.
//
// `designLog` and the per-section dimension subset are optional: the
// authoring-time page passes the live design-agent log so the
// "From you" attribution callouts show alongside each generated
// artifact; the post-creation Spec tab passes the same log read back
// from `getWorkflowAnatomy` so the attribution survives the round
// trip into the live workflow surface.

export const SIM_DIMENSIONS: readonly DesignDimension[] = [
 'goal_and_scope',
 'trigger_and_cadence',
]
export const EVAL_DIMENSIONS: readonly DesignDimension[] = [
 'goal_and_scope',
 'eval_seed_cases',
 'success_metric',
]
export const METRIC_DIMS: readonly DesignDimension[] = ['success_metric']
export const UI_DIMENSIONS: readonly DesignDimension[] = [
 'operate_ui_primitives',
]

const EVAL_TABLE_PREVIEW_LIMIT = 8

// `derived from "<phrase>"` or `inferred from <pattern>` line under a
// tool / persona / generator / metric. Null when the kernel didn't
// attach provenance (legacy rows; hand-authored items).
export function ProvenanceLine({
 provenance,
}: {
 provenance?: SpecProvenance | null
}) {
 if (!provenance) return null
 const isDerived = provenance.kind === 'derived'
 return (
 <div className="artifact-derived">
 {isDerived ? (
 <>
 derived from <em>&ldquo;{provenance.source}&rdquo;</em>
 </>
 ) : (
 <>
 inferred from <em>{provenance.source}</em>
 </>
 )}
 </div>
 )
}

export function SectionShell({
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

export function SimulatorSection(props: {
 meta: string
 tools: AgentToolSpec[]
 personas: PersonaSpec[]
 envGenerators: EnvGeneratorSpec[]
 dataSources: DataSourceSpec[]
 attributionSlot?: React.ReactNode
 action?: React.ReactNode
}) {
 const {
 meta,
 tools,
 personas,
 envGenerators,
 dataSources,
 attributionSlot,
 action,
 } = props
 const empty =
 tools.length === 0 &&
 personas.length === 0 &&
 envGenerators.length === 0 &&
 dataSources.length === 0
 return (
 <SectionShell title="Agent environment" meta={meta} action={action}>
 {attributionSlot}
 {empty ? (
 <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
 The agent environment hasn&apos;t been generated yet — Revise
 to edit the description, or continue and the iteration runner
 will generate it lazily.
 </p>
 ) : (
 <>
 {tools.length > 0 ? (
 <>
 <div className="sub-block-label">Tools the agent can call</div>
 <div className="artifact-list" style={{ marginBottom: 6 }}>
 {tools.map((t, i) => (
 <div key={`${t.name}-${i}`} className="artifact">
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
 {envGenerators.map((g, i) => (
 <div key={`${g.name}-${i}`} className="artifact">
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

function CategoryPill({ category }: { category: string | null }) {
 if (category === 'past-miss') {
 return <span className="pill amber">Past miss</span>
 }
 if (category === 'inferred') {
 return <span className="pill outline">Inferred</span>
 }
 return <span className="pill outline">Manual</span>
}

export function EvalCasesSection({
 cases,
 wsId,
 wfId,
 emptyAction,
 attributionSlot,
 action,
}: {
 cases: EvalCaseSummary[]
 wsId: string
 wfId: string
 emptyAction?: React.ReactNode
 attributionSlot?: React.ReactNode
 action?: React.ReactNode
}) {
 const total = cases.length
 if (total === 0) {
 return (
 <SectionShell title="Eval cases" meta="0 generated" action={action}>
 {attributionSlot}
 <div className="review-eval-empty">
 <p>
 No eval cases generated yet. The improvement loop needs them
 to score iterations. Generate now (one LLM call, ~25-40s) or
 skip and run the first iteration to trigger generation
 lazily.
 </p>
 {emptyAction}
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

 const shown = cases.slice(0, EVAL_TABLE_PREVIEW_LIMIT)
 const remaining = total - shown.length

 return (
 <SectionShell
 title={`Eval cases · ${total} generated`}
 meta={meta}
 action={action}
 >
 {attributionSlot}
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
 From:{' '}
 <em>
 &ldquo;{c.expected_behavior_provenance.source}
 &rdquo;
 </em>
 </>
 ) : (
 <>
 Pattern:{' '}
 <em>{c.expected_behavior_provenance.source}</em>
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
 <span
 className={`pill ${c.is_test_fold ? 'accent' : 'outline'}`}
 >
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

export function MetricSection({
 metric,
 attributionSlot,
 action,
}: {
 metric: MetricDefinitionShape | null
 attributionSlot?: React.ReactNode
 action?: React.ReactNode
}) {
 if (!metric) {
 return (
 <SectionShell
 title="Success metric"
 meta="not generated yet"
 action={action}
 >
 {attributionSlot}
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
 <SectionShell title="Success metric" meta={meta} action={action}>
 {attributionSlot}
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

export function ViewsSection({
 views,
 operateHref,
 skillCount,
 attributionSlot,
 action,
}: {
 views: unknown[]
 operateHref: string
 skillCount: number
 attributionSlot?: React.ReactNode
 action?: React.ReactNode
}) {
 const items = views
 .map((p) => {
 if (typeof p !== 'object' || p === null) return null
 const t = (p as { type?: unknown }).type
 return typeof t === 'string' ? t : null
 })
 .filter((s): s is string => s !== null)
 const meta =
 items.length > 0
 ? `${items.length} view${items.length === 1 ? '' : 's'} selected · ${skillCount} skill${skillCount === 1 ? '' : 's'} registered`
 : 'no views configured yet'
 const previewAction = (
 <Link
 href={operateHref}
 className="btn btn-secondary"
 style={{ fontSize: 12 }}
 >
 Preview layout →
 </Link>
 )
 return (
 <SectionShell
 title="Operate-view UI"
 meta={meta}
 action={action ?? previewAction}
 >
 {attributionSlot}
 {items.length === 0 ? (
 <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
 NL-gen didn&apos;t pick any UI views for this workflow.
 The Operate tab will render an empty state until a view
 is added to the spec.
 </p>
 ) : (
 <div className="view-tile-grid">
 {items.map((t, i) => (
 <div key={`${t}-${i}`} className="view-tile selected">
 <div className="view-tile-icon">{t.slice(0, 1)}</div>
 <span className="view-tile-name">{t}</span>
 <span className="view-tile-check">✓</span>
 </div>
 ))}
 </div>
 )}
 </SectionShell>
 )
}

// Helper to compute the "1 tool · 2 personas · 3 environment sources"
// summary line shared by the review page and the Spec tab.
export function simulatorMeta(
 tools: AgentToolSpec[],
 personas: PersonaSpec[],
 envGenerators: EnvGeneratorSpec[],
 dataSources: DataSourceSpec[],
 hasSimPlan: boolean,
): string {
 const totalSimItems =
 tools.length +
 personas.length +
 envGenerators.length +
 dataSources.length
 if (totalSimItems === 0) return 'environment not generated yet'
 const envCount = envGenerators.length + dataSources.length
 return (
 `${tools.length} tool${tools.length === 1 ? '' : 's'} · ` +
 `${personas.length} persona${personas.length === 1 ? '' : 's'} · ` +
 `${envCount} environment source${envCount === 1 ? '' : 's'}` +
 (hasSimPlan ? '' : ' · replay sim pending')
 )
}
