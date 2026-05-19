// Layer-D resolver.
//
// Maps spec-declared `WorkflowSpec.ui.tabs[].primitives[]` entries to
// real runtime data. Two render contexts, intentionally separated:
//
//   context: 'overview' — improvement-loop meta. Iteration count,
//     val_score curve, per-case eval predictions (the diagnostic view
//     of what the agent is getting right/wrong against the eval suite).
//
//   context: 'operate'  — production execution. What the agent is
//     producing for real (triggered by schedules / events) against
//     live data. Eval predictions are NOT production output, so the
//     case-output-derived primitives (TableView/AlertList/KanbanBoard)
//     return empty in this context until a production-output payload
//     exists. Iteration-meta primitives (MetricCards/TimeSeriesChart)
//     stay empty too — that's loop telemetry, not execution.
//
// Iteration-derived (overview only, populated when ≥1 iteration exists):
//   * MetricCards     — iteration count, latest val_score, lift vs
//                       baseline, pending-proposal count.
//   * TimeSeriesChart — the lift curve (val_score over iteration_index).
//
// Case-output-derived (overview only, populated when caseOutputs ≠ null):
//   * TableView       — per-case prediction table, failed-first.
//   * AlertList       — failed cases as high-severity alerts.
//   * KanbanBoard     — cases columned by outcome × fold (failed-test /
//                       failed-train / passed).
//
// Empty-only (no agent payload shaped like these yet — needs a
// workflow-specific output_schema extension):
//   * ScheduleGrid     — day × shift staffing matrix.
//   * ConversationView — threaded transcript with tool calls + citations.
//   * SideBySideView   — paired before/after bodies with diff highlights.
//   * DocumentReader   — structured doc blocks + margin annotations.

import type {
  AlertItem,
  ConversationData,
  DocumentData,
  KanbanData,
  MetricCardDatum,
  ScheduleData,
  SideBySideData,
  TableData,
  TimeSeriesData,
} from '@/app/components/primitives/types'
import type {
  CaseOutputList,
  EvalCaseSummary,
  IterationPoint,
  ProposalSummary,
  WorkflowSpecShape,
} from './api'

export type ResolvedPrimitive =
  | { kind: 'MetricCards'; data: MetricCardDatum[] }
  | { kind: 'TimeSeriesChart'; data: TimeSeriesData }
  | { kind: 'TableView'; data: TableData }
  | { kind: 'AlertList'; data: AlertItem[] }
  | { kind: 'KanbanBoard'; data: KanbanData }
  | { kind: 'ScheduleGrid'; data: ScheduleData }
  | { kind: 'ConversationView'; data: ConversationData }
  | { kind: 'SideBySideView'; data: SideBySideData }
  | { kind: 'DocumentReader'; data: DocumentData }
  | { kind: 'empty'; primitiveType: string; reason: string }

export type ResolverContext = 'overview' | 'operate'

export interface ResolverInputs {
  spec: WorkflowSpecShape | null
  iterations: IterationPoint[]
  evalCases: EvalCaseSummary[]
  proposals: ProposalSummary[]
  // When present, TableView/AlertList/KanbanBoard resolve to per-case
  // data built from the latest iteration's structured agent output.
  // Null / empty falls back to the "Coming soon" empty state.
  // NOTE: these are eval-suite predictions (improvement-loop
  // diagnostics), NOT production execution output — `context: 'operate'`
  // suppresses them. See the header comment.
  caseOutputs?: CaseOutputList | null
  // Workspace slug — used to build per-trace links in the TableView's
  // case_id column. Optional: when missing, the column renders as
  // plain text (no link). D4 single-tenant means this is cosmetic in
  // URLs today; the param exists for url stability.
  wsId?: string
  // Render context. 'overview' (default) populates iteration-meta +
  // eval-prediction primitives. 'operate' keeps them empty so the
  // production-execution view doesn't masquerade loop diagnostics as
  // live output.
  context?: ResolverContext
}

export function resolvePrimitives(inputs: ResolverInputs): ResolvedPrimitive[] {
  const declared = inputs.spec?.ui?.tabs?.[0]?.primitives ?? []
  return declared.map((primitive) => resolveOne(primitive, inputs))
}

// Resolve primitives from a specific named tab in the spec UI plan.
// Returns null when the tab isn't declared so the page can render its
// own empty state (rather than falling back to tab[0] which would
// duplicate the Overview).
export function resolveTabPrimitives(
  inputs: ResolverInputs,
  tabName: string,
): ResolvedPrimitive[] | null {
  const tabs = inputs.spec?.ui?.tabs ?? []
  const tab = tabs.find(
    (t) => (t.name ?? '').toLowerCase() === tabName.toLowerCase(),
  )
  if (!tab) return null
  return (tab.primitives ?? []).map((primitive) =>
    resolveOne(primitive, inputs),
  )
}

function resolveOne(
  primitive: { type: string; [key: string]: unknown },
  inputs: ResolverInputs,
): ResolvedPrimitive {
  // Operate context = production execution. Read the agent's
  // domain-shaped `output_payload` from each case-output row and
  // render the matching primitive. Empty payloads fall through to a
  // primitive-specific empty state explaining what the agent would
  // have produced.
  if (inputs.context === 'operate') {
    return resolveFromPayload(primitive, inputs)
  }
  switch (primitive.type) {
    case 'MetricCards':
      return resolveMetricCards(inputs)
    case 'TimeSeriesChart':
      return resolveTimeSeries(inputs)
    case 'TableView':
      return resolveTableView(inputs)
    case 'AlertList':
      return resolveAlertList(inputs)
    case 'KanbanBoard':
      return resolveKanbanBoard(inputs)
    case 'ScheduleGrid':
      return resolveScheduleGrid(inputs)
    case 'ConversationView':
      return resolveConversationView(inputs)
    case 'SideBySideView':
      return resolveSideBySideView(inputs)
    case 'DocumentReader':
      return resolveDocumentReader(inputs)
    default:
      return {
        kind: 'empty',
        primitiveType: primitive.type,
        reason:
          'No layer-D resolver yet — needs structured agent output beyond bool predictions.',
      }
  }
}

function resolveMetricCards(inputs: ResolverInputs): ResolvedPrimitive {
  const { iterations, evalCases, proposals } = inputs

  const latestVal =
    iterations.length > 0 ? iterations[iterations.length - 1].val_score : null
  const previousVal =
    iterations.length > 1 ? iterations[iterations.length - 2].val_score : null

  const deltaPct =
    latestVal !== null && previousVal !== null
      ? (latestVal - previousVal) * 100
      : null

  const baselineVal = iterations.length > 0 ? iterations[0].val_score : null
  const liftPct =
    latestVal !== null && baselineVal !== null
      ? (latestVal - baselineVal) * 100
      : null

  const pendingProposals = proposals.filter(
    (p) => p.state === 'gate-passed' || p.state === 'pending',
  ).length

  const data: MetricCardDatum[] = [
    {
      label: 'Iterations',
      value: iterations.length,
      // "+N all-time" reads like a delta vs an unstated baseline; the
      // number is the absolute count since the workflow launched, so
      // name it that way.
      delta:
        iterations.length > 0
          ? {
              value:
                iterations.length === 1
                  ? 'first run'
                  : `${iterations.length} runs since launch`,
              direction: iterations.length > 1 ? 'up' : 'flat',
              scope: '',
            }
          : undefined,
    },
    {
      label: 'Latest val_score',
      value: latestVal !== null ? `${(latestVal * 100).toFixed(1)}%` : '—',
      delta:
        deltaPct !== null
          ? {
              value: `${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(1)}pp`,
              direction: deltaPct > 0 ? 'up' : deltaPct < 0 ? 'down' : 'flat',
              scope: 'vs prev',
            }
          : undefined,
    },
    {
      label: 'Lift vs baseline',
      value: liftPct !== null ? `${liftPct >= 0 ? '+' : ''}${liftPct.toFixed(1)}pp` : '—',
      delta:
        liftPct !== null && iterations.length > 1
          ? {
              value: `iter 0 → ${iterations.length - 1}`,
              direction: liftPct > 0 ? 'up' : liftPct < 0 ? 'down' : 'flat',
              scope: '',
            }
          : undefined,
    },
    {
      label: 'Pending proposals',
      value: pendingProposals,
      delta:
        evalCases.length > 0
          ? {
              value: `${evalCases.length} cases`,
              direction: 'flat',
              scope: 'in suite',
            }
          : undefined,
    },
  ]

  return { kind: 'MetricCards', data }
}

function resolveTableView(inputs: ResolverInputs): ResolvedPrimitive {
  // PLAN 8.4.10 (Phase B) — render the latest iteration's per-case
  // agent output as a table. The spec's declared `columns` are
  // advisory today (most spec authors named the workflow's eventual
  // recommendation-table columns like `account_id` / `sector` which
  // the agent doesn't emit yet); we render the structured output
  // the agent actually produces — case_id, predicted, expected,
  // pass/fail, rationale.
  const co = inputs.caseOutputs
  if (!co || co.items.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'TableView',
      reason:
        co === undefined
          ? 'Case-outputs not fetched by this page.'
          : 'No iteration has produced per-case output yet.',
    }
  }

  const columns: TableData['columns'] = [
    { key: 'case_id', label: 'Case', link_key: 'case_href' },
    { key: 'predicted', label: 'Predicted' },
    { key: 'expected', label: 'Expected' },
    { key: 'passed', label: 'Result', type: 'pill' },
    { key: 'rationale', label: 'Agent rationale', title_key: 'rationale_full' },
  ]

  const tick = (v: unknown): string =>
    v === true ? '✓' : v === false ? '✗' : '—'
  const TRUNC = 140
  const truncate = (s: string): string =>
    s.length > TRUNC ? s.slice(0, TRUNC - 1).trimEnd() + '…' : s

  const wsId = inputs.wsId
  const rows = co.items.map((it) => {
    const rationale = it.output_json?.rationale
    const full = typeof rationale === 'string' ? rationale : ''
    const href =
      wsId && it.trace_id
        ? `/workspaces/${wsId}/traces/${it.trace_id}`
        : ''
    return {
      case_id: it.case_id ?? '(unknown)',
      predicted: tick(it.output_json?.predicted),
      expected: tick(it.output_json?.expected),
      passed: it.passed ? 'pass' : 'fail',
      rationale: truncate(full),
      // Available to the component for a hover-tooltip on the cell.
      rationale_full: full,
      // Per-row link target for the first column; empty falls back to plain text.
      case_href: href,
    }
  })

  // Failed-first ordering — the operator's eye lands on what regressed.
  rows.sort((a, b) => {
    const ap = a.passed === 'pass' ? 1 : 0
    const bp = b.passed === 'pass' ? 1 : 0
    return ap - bp
  })

  const data: TableData = {
    title: `Per-case agent output · iteration #${co.iteration_index ?? '?'}`,
    summary: `${rows.length} case${rows.length === 1 ? '' : 's'} · failed first`,
    columns,
    rows,
  }
  return { kind: 'TableView', data }
}


function resolveAlertList(inputs: ResolverInputs): ResolvedPrimitive {
  // Surface the iteration's failed cases as high-severity alerts.
  // Until the agent emits a workflow-specific alert shape (separate
  // from `submit_case_output`'s structured prediction), failed cases
  // are the most reliable "things the operator should look at" signal.
  // Capped at 5 so the list doesn't duplicate the full table above.
  const co = inputs.caseOutputs
  if (!co || co.items.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'AlertList',
      reason:
        co === undefined
          ? 'Case-outputs not fetched by this page.'
          : 'No iteration has produced per-case output yet.',
    }
  }
  const failed = co.items.filter((it) => !it.passed)
  if (failed.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'AlertList',
      reason: 'No failed cases on the latest iteration.',
    }
  }
  const TRUNC = 160
  const truncate = (s: string): string =>
    s.length > TRUNC ? s.slice(0, TRUNC - 1).trimEnd() + '…' : s
  const data: AlertItem[] = failed.slice(0, 5).map((it) => {
    const rationale = it.output_json?.rationale
    const meta = typeof rationale === 'string' ? truncate(rationale) : ''
    return {
      severity: 'high',
      title: it.case_id ?? '(unknown case)',
      meta:
        meta ||
        `predicted ${String(it.output_json?.predicted)} · expected ${String(it.output_json?.expected)}`,
    }
  })
  return { kind: 'AlertList', data }
}


function resolveKanbanBoard(inputs: ResolverInputs): ResolvedPrimitive {
  // Latest iteration's cases as cards, columned by outcome × fold.
  // Three columns: Failed (test fold) — what's most operator-actionable
  // since these are the held-out generalization signal — then Failed
  // (train fold), then Passed.
  const co = inputs.caseOutputs
  if (!co || co.items.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'KanbanBoard',
      reason:
        co === undefined
          ? 'Case-outputs not fetched by this page.'
          : 'No iteration has produced per-case output yet.',
    }
  }

  const TRUNC = 110
  const truncate = (s: string): string =>
    s.length > TRUNC ? s.slice(0, TRUNC - 1).trimEnd() + '…' : s

  const colKey = (it: { passed: boolean; is_test_fold: boolean }): string => {
    if (!it.passed && it.is_test_fold) return 'failed-test'
    if (!it.passed) return 'failed-train'
    return 'passed'
  }

  const cards = co.items.map((it, idx) => {
    const rationale = it.output_json?.rationale
    const body = typeof rationale === 'string' ? truncate(rationale) : ''
    const predicted = String(it.output_json?.predicted ?? '?')
    const expected = String(it.output_json?.expected ?? '?')
    return {
      id: `${it.eval_case_id}-${idx}`,
      column_key: colKey(it),
      title: it.case_id ?? '(unknown case)',
      body,
      meta: `predicted ${predicted} · expected ${expected}`,
      tags: it.is_test_fold
        ? ([{ label: 'test fold', tone: 'amber' as const }])
        : ([{ label: 'train', tone: 'outline' as const }]),
    }
  })

  const counts = cards.reduce<Record<string, number>>((acc, c) => {
    acc[c.column_key] = (acc[c.column_key] ?? 0) + 1
    return acc
  }, {})

  const data: KanbanData = {
    columns: [
      { key: 'failed-test', label: 'Failed · test fold', count: counts['failed-test'] ?? 0 },
      { key: 'failed-train', label: 'Failed · train', count: counts['failed-train'] ?? 0 },
      { key: 'passed', label: 'Passed', count: counts['passed'] ?? 0 },
    ],
    cards,
  }
  return { kind: 'KanbanBoard', data }
}


function resolveScheduleGrid(_inputs: ResolverInputs): ResolvedPrimitive {
  // ScheduleGrid wants a day × shift staffing matrix (e.g. labour
  // management's 7-day × 3-shift target/actual grid). The agent's
  // `submit_case_output` shape today carries `{predicted, expected,
  // rationale}` — no row/col keys, no per-cell value+target. Until a
  // schedule-shaped agent payload lands (PLAN 8.4.9 follow-up: per-
  // workflow `output_schema` extension), this stays an honest empty.
  return {
    kind: 'empty',
    primitiveType: 'ScheduleGrid',
    reason:
      'Agent does not emit a schedule (rows × cols × cells) payload yet — needs a workflow-specific output_schema beyond bool predictions.',
  }
}


function resolveConversationView(_inputs: ResolverInputs): ResolvedPrimitive {
  // ConversationView wants a threaded transcript (role / text / ts /
  // citations) — see mock 09 (customer support). The agent emits a
  // single per-case rationale today, not a multi-turn dialogue, and
  // doesn't carry tool-call traces in submit_case_output. Synthesising
  // a fake transcript from the rationale would mislead the operator
  // (mock 09 shows real user↔agent turns + tool calls + citations);
  // stay honest until a transcript-shaped agent payload lands (PLAN
  // 8.4.9 follow-up).
  return {
    kind: 'empty',
    primitiveType: 'ConversationView',
    reason:
      'Agent does not emit a threaded transcript payload yet — needs a workflow-specific output_schema carrying user/agent turns + tool calls + citations.',
  }
}


function resolveSideBySideView(_inputs: ResolverInputs): ResolvedPrimitive {
  // SideBySideView wants a {left, right} pair of titled bodies with
  // optional inline highlight spans — used for contract redlines
  // (mock 10) and any "current vs proposed" comparison. The agent's
  // submit_case_output carries a single rationale, not a paired
  // before/after with span-based diff highlights. Real data lands
  // when the agent emits a proposal-shaped payload (PLAN 8.4.9
  // follow-up).
  return {
    kind: 'empty',
    primitiveType: 'SideBySideView',
    reason:
      'Agent does not emit a before/after pair payload yet — needs a workflow-specific output_schema carrying {left, right} titled bodies with highlight spans.',
  }
}


function resolveDocumentReader(_inputs: ResolverInputs): ResolvedPrimitive {
  // DocumentReader wants a structured document — heading / paragraph /
  // clause blocks with optional inline spans + a parallel list of
  // margin annotations keyed back to spans (mock 10, contract review).
  // No path through submit_case_output produces that today; the agent
  // emits at most a rationale string. Real data lands when the agent
  // emits a document-shaped payload (PLAN 8.4.9 follow-up).
  return {
    kind: 'empty',
    primitiveType: 'DocumentReader',
    reason:
      'Agent does not emit a structured document payload yet — needs a workflow-specific output_schema carrying typed blocks + margin annotations.',
  }
}


function resolveTimeSeries(inputs: ResolverInputs): ResolvedPrimitive {
  const { iterations } = inputs
  const scored = iterations.filter(
    (it): it is IterationPoint & { val_score: number } =>
      it.val_score !== null && it.val_score !== undefined,
  )

  const points = scored.map((it) => ({
    t: `iter ${it.iteration_index}`,
    value: Math.round(it.val_score * 1000) / 10, // 0.733 → 73.3
  }))

  const baseline = scored.length > 0 ? Math.round(scored[0].val_score * 1000) / 10 : undefined

  const data: TimeSeriesData = {
    title: 'Lift curve',
    subtitle:
      scored.length === 0
        ? 'no iterations recorded yet'
        : `${scored.length} iteration${scored.length === 1 ? '' : 's'} · val_score over time`,
    series: [
      {
        name: 'val_score',
        points,
      },
    ],
    y_format: 'percent',
    baseline,
    baseline_label: baseline !== undefined ? 'baseline (iter 0)' : undefined,
  }

  return { kind: 'TimeSeriesChart', data }
}


// =============================================================================
// Operate-context resolvers — render `output_payload` the agent emitted via
// predict_label's optional output_payload field. The shapes the agent fills
// match `_PRIMITIVE_PAYLOAD_GUIDE` in apps/kernel/.../eval_runner/agent_solver.py;
// agreement between that table and the readers below is what makes this round
// trip work. Each reader is defensive: if the agent emits a slightly different
// shape we keep the page rendering rather than throwing.
// =============================================================================

const PAYLOAD_KEY_BY_PRIMITIVE: Record<string, string> = {
  MetricCards: 'metrics',
  TimeSeriesChart: 'time_series',
  TableView: 'table',
  AlertList: 'alerts',
  KanbanBoard: 'kanban',
  ScheduleGrid: 'schedule',
  ConversationView: 'conversation',
  SideBySideView: 'side_by_side',
  DocumentReader: 'document',
}

function resolveFromPayload(
  primitive: { type: string; [key: string]: unknown },
  inputs: ResolverInputs,
): ResolvedPrimitive {
  const key = PAYLOAD_KEY_BY_PRIMITIVE[primitive.type]
  if (!key) {
    return {
      kind: 'empty',
      primitiveType: primitive.type,
      reason: 'Operate renderer not implemented for this primitive type yet.',
    }
  }
  const co = inputs.caseOutputs
  if (!co || co.items.length === 0) {
    return {
      kind: 'empty',
      primitiveType: primitive.type,
      reason:
        'No production run captured yet. Output renders here once the agent emits an output_payload for at least one case.',
    }
  }
  // Cases the agent annotated with a payload for THIS primitive. Empty
  // means the agent skipped this primitive for every case — show why
  // rather than rendering an empty primitive that looks broken.
  // `caseHref` is the per-case trace detail link; the operator clicks
  // any rendered card / row / alert to inspect the agent's full
  // reasoning chain for that case.
  const wsId = inputs.wsId
  const carriers = co.items
    .map((it) => ({
      caseId: it.case_id,
      caseHref:
        wsId && it.trace_id
          ? `/workspaces/${wsId}/traces/${it.trace_id}`
          : null,
      value: pickPayloadKey(it.output_payload, key),
    }))
    .filter((c): c is Carrier => c.value !== undefined)
  if (carriers.length === 0) {
    return {
      kind: 'empty',
      primitiveType: primitive.type,
      reason: `Agent did not emit \`${key}\` in output_payload for any case on the latest iteration.`,
    }
  }
  switch (primitive.type) {
    case 'MetricCards':
      return payloadToMetricCards(carriers)
    case 'TimeSeriesChart':
      return payloadToTimeSeries(carriers)
    case 'TableView':
      return payloadToTableView(carriers, co.iteration_index)
    case 'AlertList':
      return payloadToAlertList(carriers)
    case 'KanbanBoard':
      return payloadToKanban(carriers)
    case 'ScheduleGrid':
      return payloadToScheduleGrid(carriers)
    case 'ConversationView':
      return payloadToConversation(carriers)
    case 'SideBySideView':
      return payloadToSideBySide(carriers)
    case 'DocumentReader':
      return payloadToDocument(carriers)
    default:
      return {
        kind: 'empty',
        primitiveType: primitive.type,
        reason: 'Operate renderer not implemented for this primitive type yet.',
      }
  }
}

function pickPayloadKey(
  payload: Record<string, unknown> | null | undefined,
  key: string,
): unknown | undefined {
  if (!payload) return undefined
  const v = payload[key]
  if (v === undefined || v === null) return undefined
  // Empty arrays / empty objects don't carry useful content for the
  // operator, treat them as "agent emitted nothing here".
  if (Array.isArray(v) && v.length === 0) return undefined
  if (typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0) {
    return undefined
  }
  return v
}

type Carrier = {
  caseId: string | null
  caseHref: string | null
  value: unknown
}

// Caption attached to single-instance primitives (TimeSeriesChart,
// SideBySideView, etc.) so the operator can click into the case the
// payload came from. Multi-row primitives (TableView / AlertList /
// KanbanBoard) embed the link per row/item instead.
function caseCaption(c: Carrier): { text: string; href: string } | null {
  if (!c.caseHref || !c.caseId) return null
  return { text: `Source: case ${c.caseId} →`, href: c.caseHref }
}

function asObject(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null
}

function asArrayOfObjects(v: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(v)) return []
  return v.filter(
    (e): e is Record<string, unknown> =>
      e !== null && typeof e === 'object' && !Array.isArray(e),
  )
}

function asString(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

function asNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

function payloadToMetricCards(carriers: Carrier[]): ResolvedPrimitive {
  // MetricCards take a small fixed set of headline numbers. Pick the
  // first case that emitted metrics — aggregating across cases would
  // mean averaging metrics across different recommendations, which is
  // domain-specific and rarely meaningful.
  const first = carriers[0]
  const items = asArrayOfObjects(first.value)
  const data: MetricCardDatum[] = items.slice(0, 4).map((m) => {
    const value = m.value
    const label = asString(m.label) ?? 'Metric'
    const deltaPct = asNumber(m.delta_pct)
    return {
      label,
      value:
        typeof value === 'number' || typeof value === 'string' ? value : '—',
      delta:
        deltaPct !== undefined
          ? {
              value: `${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(1)}%`,
              direction: deltaPct > 0 ? 'up' : deltaPct < 0 ? 'down' : 'flat',
              scope: '',
            }
          : undefined,
    }
  })
  if (data.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'MetricCards',
      reason: 'Agent emitted `metrics` but with no usable items.',
    }
  }
  return { kind: 'MetricCards', data }
}

function payloadToTimeSeries(carriers: Carrier[]): ResolvedPrimitive {
  // Each case may emit its own forecast curve (e.g. one per SKU). Show
  // the first case's curve and label with the case_id. Future: pick up
  // to N cases as additional series.
  const first = carriers[0]
  const obj = asObject(first.value)
  if (!obj) {
    return {
      kind: 'empty',
      primitiveType: 'TimeSeriesChart',
      reason: 'Agent emitted `time_series` but it was not an object.',
    }
  }
  const series = asArrayOfObjects(obj.series).map((s) => ({
    name: asString(s.name) ?? 'series',
    points: asArrayOfObjects(s.points)
      .map((p) => {
        const t = asString(p.t)
        const value = asNumber(p.value)
        return t !== undefined && value !== undefined ? { t, value } : null
      })
      .filter((p): p is { t: string; value: number } => p !== null),
  }))
  if (series.length === 0 || series.every((s) => s.points.length === 0)) {
    return {
      kind: 'empty',
      primitiveType: 'TimeSeriesChart',
      reason: 'Agent emitted `time_series` but with no usable points.',
    }
  }
  const yFormat = asString(obj.y_format)
  const data: TimeSeriesData = {
    title: asString(obj.title) ?? 'Forecast',
    subtitle: first.caseId ? `case ${first.caseId}` : undefined,
    series,
    y_format:
      yFormat === 'percent' || yFormat === 'currency' || yFormat === 'number'
        ? yFormat
        : 'number',
  }
  const cap = caseCaption(first)
  if (cap) data.caption = cap
  return { kind: 'TimeSeriesChart', data }
}

function payloadToTableView(
  carriers: Carrier[],
  iterationIndex: number | null,
): ResolvedPrimitive {
  // Aggregate rows from every case that emitted a table. Use the first
  // table's columns as the canonical schema — extra keys in later rows
  // are kept on the row dict (TableView shows declared columns only).
  // Append a synthetic "Source" column linking to the per-case trace
  // so the operator can drill into the agent's full reasoning chain
  // for any row.
  let columns: TableColumn[] = []
  const rows: TableData['rows'] = []
  let anyHref = false
  for (const c of carriers) {
    const obj = asObject(c.value)
    if (!obj) continue
    if (columns.length === 0) {
      columns = asArrayOfObjects(obj.columns).map((col) => ({
        key: asString(col.key) ?? 'col',
        label: asString(col.label) ?? '',
      }))
    }
    for (const row of asArrayOfObjects(obj.rows)) {
      const enriched: TableData['rows'][number] = { ...row }
      if (c.caseHref) {
        enriched._case_id = c.caseId ?? ''
        enriched._case_href = c.caseHref
        anyHref = true
      }
      rows.push(enriched)
    }
  }
  if (columns.length === 0 || rows.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'TableView',
      reason: 'Agent emitted `table` but with no usable columns/rows.',
    }
  }
  if (anyHref) {
    columns.push({
      key: '_case_id',
      label: 'Source case',
      link_key: '_case_href',
    })
  }
  const data: TableData = {
    title: 'Agent recommendations',
    summary: `${rows.length} row${rows.length === 1 ? '' : 's'}${
      iterationIndex !== null ? ` · iteration #${iterationIndex}` : ''
    } · click a row's source case for the full agent reasoning trace`,
    columns,
    rows,
  }
  return { kind: 'TableView', data }
}

function payloadToAlertList(carriers: Carrier[]): ResolvedPrimitive {
  // Concatenate alerts across cases. Cap at 8 so the list stays
  // scannable — operator drills into the table for the full set.
  // Each alert carries its case's trace URL via `action_url` so the
  // operator can jump straight to the source.
  const data: AlertItem[] = []
  for (const c of carriers) {
    for (const a of asArrayOfObjects(c.value)) {
      const severity = asString(a.severity)
      const title = asString(a.title)
      if (!title) continue
      const item: AlertItem = {
        severity:
          severity === 'high' || severity === 'medium' || severity === 'low'
            ? severity
            : 'medium',
        title,
        meta: asString(a.meta) ?? '',
      }
      if (c.caseHref) item.action_url = c.caseHref
      data.push(item)
      if (data.length >= 8) break
    }
    if (data.length >= 8) break
  }
  if (data.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'AlertList',
      reason: 'Agent emitted `alerts` but with no usable items.',
    }
  }
  return { kind: 'AlertList', data }
}

function payloadToKanban(carriers: Carrier[]): ResolvedPrimitive {
  // Use the first case's column definitions; aggregate cards from
  // every case. Cards with column_key that doesn't match a column get
  // dropped (mismatched schema would render an orphan column).
  const first = asObject(carriers[0].value)
  if (!first) {
    return {
      kind: 'empty',
      primitiveType: 'KanbanBoard',
      reason: 'Agent emitted `kanban` but it was not an object.',
    }
  }
  const columnDefs = asArrayOfObjects(first.columns).map((c) => ({
    key: asString(c.key) ?? '',
    label: asString(c.label) ?? '',
  }))
  const colKeys = new Set(columnDefs.map((c) => c.key).filter((k) => k))
  const cards: KanbanData['cards'] = []
  for (const c of carriers) {
    const obj = asObject(c.value)
    if (!obj) continue
    asArrayOfObjects(obj.cards).forEach((card, i) => {
      const columnKey = asString(card.column_key)
      const title = asString(card.title)
      if (!columnKey || !colKeys.has(columnKey) || !title) return
      const built: KanbanData['cards'][number] = {
        id: `${c.caseId ?? 'c'}-${i}`,
        column_key: columnKey,
        title,
        body: asString(card.body) ?? '',
        meta: asString(card.meta) ?? '',
      }
      if (c.caseHref) built.href = c.caseHref
      cards.push(built)
    })
  }
  if (columnDefs.length === 0 || cards.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'KanbanBoard',
      reason: 'Agent emitted `kanban` but with no usable columns/cards.',
    }
  }
  const counts = cards.reduce<Record<string, number>>((acc, c) => {
    acc[c.column_key] = (acc[c.column_key] ?? 0) + 1
    return acc
  }, {})
  const data: KanbanData = {
    columns: columnDefs.map((c) => ({ ...c, count: counts[c.key] ?? 0 })),
    cards,
  }
  return { kind: 'KanbanBoard', data }
}

function payloadToScheduleGrid(carriers: Carrier[]): ResolvedPrimitive {
  // Single case's schedule (rows × cols × cells). Aggregating across
  // cases would silently merge different staffing weeks.
  const obj = asObject(carriers[0].value)
  if (!obj) {
    return {
      kind: 'empty',
      primitiveType: 'ScheduleGrid',
      reason: 'Agent emitted `schedule` but it was not an object.',
    }
  }
  const rowLabels = Array.isArray(obj.row_labels)
    ? (obj.row_labels.filter((v) => typeof v === 'string') as string[])
    : []
  const colLabels = Array.isArray(obj.col_labels)
    ? (obj.col_labels.filter((v) => typeof v === 'string') as string[])
    : []
  const cellsRaw = asArrayOfObjects(obj.cells)
  if (rowLabels.length === 0 || colLabels.length === 0 || cellsRaw.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'ScheduleGrid',
      reason: 'Agent emitted `schedule` but it was missing rows/cols/cells.',
    }
  }
  const data: ScheduleData = {
    rows: rowLabels.map((label) => ({ key: label, label })),
    cols: colLabels.map((label) => ({ key: label, label })),
    cells: cellsRaw
      .map((c): ScheduleCellDef | null => {
        const row = asString(c.row)
        const col = asString(c.col)
        const value = c.value
        if (!row || !col || (typeof value !== 'number' && typeof value !== 'string')) {
          return null
        }
        const target = c.target
        const numericValue = typeof value === 'number' ? value : Number(value)
        const numericTarget = typeof target === 'number' ? target : Number(target)
        const status: ScheduleCellStatus =
          Number.isFinite(numericTarget) && Number.isFinite(numericValue)
            ? numericValue < numericTarget
              ? 'warn'
              : 'ok'
            : 'ok'
        const cell: ScheduleCellDef = { row_key: row, col_key: col, value, status }
        if (typeof target === 'number' || typeof target === 'string') {
          cell.target = target
        }
        return cell
      })
      .filter((c): c is ScheduleCellDef => c !== null),
  }
  const cap = caseCaption(carriers[0])
  if (cap) data.caption = cap
  return { kind: 'ScheduleGrid', data }
}

type ScheduleCellDef = ScheduleData['cells'][number]
type ScheduleCellStatus = ScheduleCellDef['status']
type TableColumn = TableData['columns'][number]

function payloadToConversation(carriers: Carrier[]): ResolvedPrimitive {
  // Threaded transcript — first case only.
  const obj = asObject(carriers[0].value)
  if (!obj) {
    return {
      kind: 'empty',
      primitiveType: 'ConversationView',
      reason: 'Agent emitted `conversation` but it was not an object.',
    }
  }
  type Msg = ConversationData['messages'][number]
  const messages: Msg[] = asArrayOfObjects(obj.turns)
    .map((t): Msg | null => {
      const role = asString(t.role)
      const text = asString(t.text)
      if (!text) return null
      const r: Msg['role'] = role === 'user' || role === 'system' ? role : 'agent'
      const msg: Msg = { role: r, text }
      const ts = asString(t.ts)
      if (ts !== undefined) msg.ts = ts
      return msg
    })
    .filter((m): m is Msg => m !== null)
  if (messages.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'ConversationView',
      reason: 'Agent emitted `conversation` but with no usable turns.',
    }
  }
  const data: ConversationData = { messages }
  const cap = caseCaption(carriers[0])
  if (cap) data.caption = cap
  return { kind: 'ConversationView', data }
}

function payloadToSideBySide(carriers: Carrier[]): ResolvedPrimitive {
  // Pick the first carrier that has both left+right populated. Falls
  // back to whatever the first non-empty case emits, even if one side
  // is missing (we synthesise the other side as a placeholder).
  for (const c of carriers) {
    const obj = asObject(c.value)
    if (!obj) continue
    const left = asObject(obj.left)
    const right = asObject(obj.right)
    const leftTitle = asString(left?.title) ?? 'Original'
    const leftBody = asString(left?.body) ?? ''
    const rightTitle = asString(right?.title) ?? 'Proposed'
    const rightBody = asString(right?.body) ?? ''
    if (leftBody || rightBody) {
      const data: SideBySideData = {
        left: { title: leftTitle, body: leftBody, format: 'text' },
        right: { title: rightTitle, body: rightBody, format: 'text' },
      }
      const cap = caseCaption(c)
      if (cap) data.caption = cap
      return { kind: 'SideBySideView', data }
    }
  }
  return {
    kind: 'empty',
    primitiveType: 'SideBySideView',
    reason: 'Agent emitted `side_by_side` but with no usable left/right bodies.',
  }
}

function payloadToDocument(carriers: Carrier[]): ResolvedPrimitive {
  const obj = asObject(carriers[0].value)
  if (!obj) {
    return {
      kind: 'empty',
      primitiveType: 'DocumentReader',
      reason: 'Agent emitted `document` but it was not an object.',
    }
  }
  const blocks: DocumentData['blocks'] = asArrayOfObjects(obj.blocks)
    .map((b) => {
      const text = asString(b.text)
      if (!text) return null
      const t = asString(b.type)
      const kind: DocumentData['blocks'][number]['kind'] =
        t === 'heading' ? 'heading' : t === 'clause' ? 'clause' : 'para'
      return { kind, text }
    })
    .filter((b): b is DocumentData['blocks'][number] => b !== null)
  type Ann = DocumentData['annotations'][number]
  const annotations: Ann[] = asArrayOfObjects(obj.annotations)
    .map((a, i): Ann | null => {
      const text = asString(a.text)
      if (!text) return null
      const kind = asString(a.kind)
      const sev: Ann['severity'] =
        kind === 'issue' ? 'high' : kind === 'suggest' ? 'medium' : 'low'
      const ann: Ann = {
        id: `a-${i}`,
        severity: sev,
        title: kind ?? 'note',
        body: text,
      }
      const blockId = asString(a.block_id)
      if (blockId !== undefined) ann.span_id = blockId
      return ann
    })
    .filter((a): a is Ann => a !== null)
  if (blocks.length === 0) {
    return {
      kind: 'empty',
      primitiveType: 'DocumentReader',
      reason: 'Agent emitted `document` but with no usable blocks.',
    }
  }
  const data: DocumentData = { blocks, annotations }
  const cap = caseCaption(carriers[0])
  if (cap) data.caption = cap
  return { kind: 'DocumentReader', data }
}
