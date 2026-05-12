// Layer-D resolver (PLAN row 8.4.6).
//
// Maps spec-declared `WorkflowSpec.ui.tabs[0].primitives[]` entries to
// real runtime data derived from the workflow's iteration history. No
// hand-curated mocks, no hard-coded fallbacks — what the agent has
// actually produced gets surfaced; everything else stays an empty state.
//
// What we DO derive:
//   * MetricCards    — iteration count, latest val_score, eval-case
//                      totals, proposal counts.
//   * TimeSeriesChart — the lift curve (val_score over iteration_index).
//
// What we DON'T derive (returns null — UI renders an empty placeholder):
//   * TableView / AlertList / KanbanBoard / ScheduleGrid /
//     ConversationView / SideBySideView / DocumentReader.
//   These need richer agent output (per-row predictions, audit-derived
//   alerts, etc.) that the current NL-gen iteration loop doesn't emit.
//   Plumbing them is future work — when the agent's per-case output
//   gets structured beyond `bool prediction`.

import type {
  MetricCardDatum,
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
    { key: 'case_id', label: 'Case' },
    { key: 'predicted', label: 'Predicted' },
    { key: 'expected', label: 'Expected' },
    { key: 'passed', label: 'Result', type: 'pill' },
    { key: 'rationale', label: 'Agent rationale' },
  ]

  const tick = (v: unknown): string =>
    v === true ? '✓' : v === false ? '✗' : '—'

  const rows = co.items.map((it) => {
    const rationale = it.output_json?.rationale
    return {
      case_id: it.case_id ?? '(unknown)',
      predicted: tick(it.output_json?.predicted),
      expected: tick(it.output_json?.expected),
      passed: it.passed ? 'pass' : 'fail',
      rationale: typeof rationale === 'string' ? rationale : '',
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
