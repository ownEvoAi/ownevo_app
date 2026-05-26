import type { DesignAgentLog, DesignAgentLogEntry, DesignDimension } from '@/lib/api'

// Per-section attribution callout — renders the design-agent answers
// that shaped THIS section's generated content. Reads the persisted
// `design_agent_log` from the workflow anatomy and filters to a
// per-section dimension subset (mirrors the slice-3 generator
// subsets in `apps/kernel/.../nl_gen/design_brief_context.py`).
//
// Returns null when:
// * no design_agent_log on this workflow (operator skipped the
// discovery interview), OR
// * none of the operator's answers targeted dimensions this section
// was generated from.
// Either case → render nothing (no callout is better than an empty
// "From your discovery answers" header).

// Same back-compat mapping used kernel-side. Legacy entries (pre-LLM
// interviewer) carry only `kind`, not `dimension`; the mapping routes
// them to the closest dimension so attribution still works on rows
// authored before the dimension-aware interview shipped.
const KIND_TO_DIMENSION: Record<string, DesignDimension> = {
 metric: 'success_metric',
 trigger: 'trigger_and_cadence',
 surface: 'operate_ui_primitives',
 ambiguity: 'goal_and_scope',
 premise: 'goal_and_scope',
}

// Display labels — kept in sync with the strip on /workflows/new/design.
const DIMENSION_LABEL: Record<DesignDimension, string> = {
 goal_and_scope: 'Goal & scope',
 trigger_and_cadence: 'Trigger & cadence',
 data_sources_and_connectors: 'Data sources',
 success_metric: 'Success metric',
 eval_seed_cases: 'Eval seed cases',
 operate_ui_primitives: 'Operate UI',
 reviewer_role: 'Reviewer',
}

function entryDimension(entry: DesignAgentLogEntry): DesignDimension | null {
 if (entry.dimension) return entry.dimension
 return KIND_TO_DIMENSION[entry.kind] ?? null
}

function isAnswered(entry: DesignAgentLogEntry): boolean {
 const hasOption = (entry.chosen_option ?? '').trim.length > 0
 const hasText = (entry.answer ?? '').trim.length > 0
 return hasOption || hasText
}

function answerSummary(entry: DesignAgentLogEntry): string {
 const option = (entry.chosen_option ?? '').trim const text = (entry.answer ?? '').trim if (option && text) return `${option} · "${text}"`
 return option || text || '(skipped)'
}

interface Props {
 log: DesignAgentLog | null | undefined
 // Dimension subset this section was generated from. Mirrors the
 // kernel-side subset constants (SPEC_DIMENSIONS, METRIC_DIMENSIONS,
 // etc.). Order in the prop = render order in the callout.
 dimensions: readonly DesignDimension[]
}

export function DesignAttribution({ log, dimensions }: Props) {
 if (!log) return null
 const wanted = new Set<DesignDimension>(dimensions)
 const matched: DesignAgentLogEntry[] = []
 for (const entry of log.discovery_transcript) {
 if (!isAnswered(entry)) continue
 const dim = entryDimension(entry)
 if (dim && wanted.has(dim)) {
 matched.push(entry)
 }
 }
 if (matched.length === 0) return null
 return (
 <div
 className="design-attribution"
 role="note"
 aria-label="Design-agent answers that shaped this section"
 >
 <div className="design-attribution-header">
 From your discovery answers
 </div>
 <ul className="design-attribution-list">
 {matched.map((entry, i) => {
 const dim = entryDimension(entry)
 const label =
 (dim && DIMENSION_LABEL[dim]) || entry.kind || 'Question'
 return (
 <li key={`${entry.question_index}-${i}`}>
 <span className="design-attribution-dim">{label}:</span>{' '}
 <span className="design-attribution-answer">
 {answerSummary(entry)}
 </span>
 </li>
 )
 })}
 </ul>
 </div>
 )
}
