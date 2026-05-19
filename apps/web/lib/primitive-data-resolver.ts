// Layer-D resolver (PLAN row 8.4.6).
//
// Maps spec-declared `WorkflowSpec.ui.tabs[].primitives[]` entries to
// real runtime data derived from the workflow's iteration history +
// per-case agent outputs. No hand-curated mocks, no hard-coded
// fallbacks — what the agent has actually produced gets surfaced;
// everything else returns a primitive-specific `empty` reason that
// the page renders as a "Coming soon" callout.
//
// Iteration-derived (always populated when ≥1 iteration exists):
//   * MetricCards     — iteration count, latest val_score, lift vs
//                       baseline, pending-proposal count.
//   * TimeSeriesChart — the lift curve (val_score over iteration_index).
//
// Case-output-derived (PLAN 8.4.10, populated when caseOutputs ≠ null):
//   * TableView       — per-case prediction table, failed-first.
//   * AlertList       — failed cases as high-severity alerts.
//   * KanbanBoard     — cases columned by outcome × fold (failed-test /
//                       failed-train / passed).
//
// Empty-only (await PLAN 8.4.9 follow-up: workflow-specific
// `output_schema` extension so the agent can emit these shapes):
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

export interface ResolverInputs {
  spec: WorkflowSpecShape | null
  iterations: IterationPoint[]
  evalCases: EvalCaseSummary[]
  proposals: ProposalSummary[]
  // PLAN 8.4.10 (Phase B) — when present, TableView resolves to a
  // per-case table built from the latest iteration's structured agent
  // output. Null / empty falls back to the "Coming soon" empty state.
  caseOutputs?: CaseOutputList | null
  // Workspace slug — used to build per-trace links in the TableView's
  // case_id column. Optional: when missing, the column renders as
  // plain text (no link). D4 single-tenant means this is cosmetic in
  // URLs today; the param exists for url stability.
  wsId?: string
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
