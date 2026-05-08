// W7 slice 6 — static mock data for the three positioning workflows
// (labour / contract / support). The Overview page reads from this
// file when wfId matches one of the keys here; demand-prediction is
// live, everything else falls back to a "workflow not configured"
// state.
//
// Mocks read like real data but every number is hand-authored. Every
// page that renders them surfaces a MOCK banner so reviewers can't
// confuse them with the live demand-prediction flow. Same shape as a
// future live Overview so swapping live data in is a one-line change.

import type {
  AgentToolSpec,
  ReviewerSpec,
  SkillSummary,
  WorkflowSpecShape,
} from '@/lib/api'

export interface MockAnatomy {
  // Same shape the live page assembles from the kernel API; the
  // anatomy pane component is data-shape-agnostic.
  skills: SkillSummary[]
  spec: WorkflowSpecShape
}

export interface WorkflowMock {
  id: string
  title: string
  buyer: string
  buyerRole: string
  status: 'active' | 'pilot' | 'paused'
  version: string
  description: string
  metrics: Array<{
    label: string
    value: string
    // `tone` drives the colour of the delta cell (positive = green,
    // negative = red, neutral = muted). It describes the BUSINESS
    // outcome, NOT the numeric direction — fewer compliance breaches
    // is a positive tone even though the number went ▼ down. The
    // arrow glyph (▲ / ▼) lives inside `text` and reflects the
    // raw numeric movement.
    delta?: { tone: 'positive' | 'negative' | 'neutral'; text: string }
  }>
  recentActivity: Array<{
    kind: 'approval' | 'cluster' | 'regression' | 'escalation'
    body: string
    when: string
  }>
  anatomy: MockAnatomy
}

function mockSkill(
  id: string,
  kind: SkillSummary['kind'],
  versionSeq: number,
  tags: string[],
): SkillSummary {
  return {
    id,
    kind,
    workflow_id: null,
    capability_tags: tags,
    head_version_id: null,
    head_version_seq: versionSeq,
    head_created_at: null,
  }
}

function mockTool(
  name: string,
  description: string,
  inputs: AgentToolSpec['inputs'] = [],
  outputs: AgentToolSpec['outputs'] = [],
): AgentToolSpec {
  return { name, description, inputs, outputs }
}

const SINGLE_AGENT_REVIEWER = (role: string, cadence: string): ReviewerSpec => ({
  role,
  cadence,
  description: '',
})

export const WORKFLOW_MOCKS: Record<string, WorkflowMock> = {
  labour: {
    id: 'labour',
    title: 'Labour management',
    buyer: 'Devon Park',
    buyerRole: 'Workforce Ops Director',
    status: 'active',
    version: 'v17',
    description:
      'Shift-validation and overtime-cap enforcement across 480 weekly schedules.',
    metrics: [
      { label: 'Schedules / week', value: '482', delta: { tone: 'neutral', text: 'unchanged' } },
      { label: 'Compliance breaches', value: '3', delta: { tone: 'positive', text: '▼ 11 vs last week' } },
      { label: 'Approval rate', value: '94.1%', delta: { tone: 'positive', text: '▲ 2.3 pts' } },
      { label: 'Cluster eval cases', value: '24', delta: { tone: 'neutral', text: 'auto-promoted' } },
    ],
    recentActivity: [
      { kind: 'approval', body: 'Approved “Hard cap on weekend doubles in Pacific NW”', when: '2h ago' },
      { kind: 'cluster', body: 'New cluster surfaced: “Late-shift swap not flagged for skill mismatch”', when: '6h ago' },
      { kind: 'regression', body: 'Gate blocked v17.3: forecast_overtime regressed on 2 prior cases', when: '1d ago' },
    ],
    anatomy: {
      skills: [
        mockSkill('labour.shift_validator', 'python', 17,
          ['scheduling', 'compliance']),
        mockSkill('labour.overtime_cap_policy', 'instruction', 9,
          ['policy', 'overtime']),
        mockSkill('labour.swap_eligibility', 'python', 4,
          ['scheduling', 'fairness']),
      ],
      spec: {
        domain: 'workforce-ops',
        environment: {
          entities: [{ name: 'shift' }, { name: 'employee' }, { name: 'site' }],
          data_sources: [{ id: 'wfm_csv' }, { id: 'cba_clauses' }],
          seasonality: ['weekly', 'holiday'],
        },
        tools: [
          mockTool('lookup_employee', 'Read employee skills + certifications',
            [{ name: 'employee_id', type: 'string' }],
            [{ name: 'profile', type: 'object' }]),
          mockTool('check_cba_rule', 'Match a proposed shift against CBA clauses',
            [{ name: 'shift', type: 'object' }],
            [{ name: 'violations', type: 'array' }]),
          mockTool('emit_decision', 'Record the validated schedule outcome',
            [{ name: 'schedule', type: 'object' }, { name: 'rationale', type: 'string' }]),
        ],
        reviewer: SINGLE_AGENT_REVIEWER('Workforce Ops Director', 'on regression-only'),
        success_criterion: {
          direction: 'minimize',
          target_metric_name: 'compliance_breach_rate',
          description: 'Hard violations of CBA + state regs across all 482 weekly schedules.',
        },
      },
    },
  },
  contract: {
    id: 'contract',
    title: 'Union contract review',
    buyer: 'Priya Iyer',
    buyerRole: 'General Counsel',
    status: 'pilot',
    version: 'v6',
    description:
      'Clause-by-clause review of bargaining-unit agreements; surfaces conflicts with active CBAs.',
    metrics: [
      { label: 'Contracts / month', value: '38', delta: { tone: 'positive', text: '▲ 6 vs last month' } },
      { label: 'Reviewer hours saved', value: '162h', delta: { tone: 'positive', text: '▲ 28h' } },
      { label: 'False-flag rate', value: '4.7%', delta: { tone: 'negative', text: '▲ 1.1 pts' } },
      { label: 'Cluster eval cases', value: '11', delta: { tone: 'neutral', text: 'auto-promoted' } },
    ],
    recentActivity: [
      { kind: 'escalation', body: 'Counsel escalation: ambiguous overtime clause in Local 218', when: '4h ago' },
      { kind: 'approval', body: 'Approved “Recognize ‘pyramiding’ as discrete pay-stacking pattern”', when: '1d ago' },
      { kind: 'cluster', body: 'New cluster: “Severance triggers for fixed-term renewals”', when: '2d ago' },
    ],
    anatomy: {
      skills: [
        mockSkill('contract.clause_parser', 'python', 6, ['legal', 'parsing']),
        mockSkill('contract.conflict_policy', 'instruction', 3, ['legal', 'policy']),
      ],
      spec: {
        domain: 'legal',
        environment: {
          entities: [{ name: 'contract' }, { name: 'cba' }, { name: 'clause' }],
          data_sources: [{ id: 'docusign_corpus' }, { id: 'active_cbas' }],
        },
        tools: [
          mockTool('extract_clauses', 'Parse a contract into clause tree',
            [{ name: 'contract_id', type: 'string' }],
            [{ name: 'clauses', type: 'array' }]),
          mockTool('match_against_cba', 'Find CBA clauses that conflict',
            [{ name: 'clause', type: 'object' }],
            [{ name: 'conflicts', type: 'array' }]),
          mockTool('flag_for_counsel', 'Escalate ambiguous clauses to GC',
            [{ name: 'clause_id', type: 'string' }, { name: 'reason', type: 'string' }]),
        ],
        reviewer: SINGLE_AGENT_REVIEWER('General Counsel', 'on escalation'),
        success_criterion: {
          direction: 'minimize',
          target_metric_name: 'false_flag_rate',
          description: 'Conflict-flags that counsel resolves as non-issues.',
        },
      },
    },
  },
  support: {
    id: 'support',
    title: 'Customer support triage',
    buyer: 'Nadia Romero',
    buyerRole: 'VP Customer Success',
    status: 'active',
    version: 'v22',
    description:
      'Tier-2 ticket triage; assigns severity + routes to specialist queues based on conversation transcript.',
    metrics: [
      { label: 'Tickets / day', value: '1,820', delta: { tone: 'neutral', text: 'unchanged' } },
      { label: 'P1 mis-routes', value: '7', delta: { tone: 'positive', text: '▼ 14 vs last week' } },
      { label: 'Time-to-route p95', value: '38s', delta: { tone: 'positive', text: '▼ 9s' } },
      { label: 'Cluster eval cases', value: '53', delta: { tone: 'neutral', text: 'auto-promoted' } },
    ],
    recentActivity: [
      { kind: 'approval', body: 'Approved “Treat ‘billing dispute >30d’ as P1 by default”', when: '1h ago' },
      { kind: 'cluster', body: 'New cluster: “Refund requests with order-number typos”', when: '5h ago' },
      { kind: 'approval', body: 'Approved “Route enterprise contract questions to legal-cs”', when: '3d ago' },
    ],
    anatomy: {
      skills: [
        mockSkill('support.severity_classifier', 'python', 22,
          ['triage', 'severity']),
        mockSkill('support.routing_policy', 'instruction', 14, ['routing']),
        mockSkill('support.resolution_predictor', 'python', 11,
          ['triage', 'forecasting']),
      ],
      spec: {
        domain: 'customer-success',
        environment: {
          entities: [{ name: 'ticket' }, { name: 'customer' }, { name: 'queue' }],
          data_sources: [{ id: 'zendesk_threads' }, { id: 'salesforce_accounts' }],
        },
        tools: [
          mockTool('lookup_account_tier', 'Read customer plan + entitlements',
            [{ name: 'account_id', type: 'string' }],
            [{ name: 'tier', type: 'string' }]),
          mockTool('classify_severity', 'Assign P1/P2/P3 from transcript',
            [{ name: 'transcript', type: 'string' }],
            [{ name: 'severity', type: 'string' }]),
          mockTool('route_to_queue', 'Push the ticket onto a specialist queue',
            [{ name: 'ticket_id', type: 'string' }, { name: 'queue', type: 'string' }]),
        ],
        reviewer: SINGLE_AGENT_REVIEWER('VP Customer Success', 'weekly batch'),
        success_criterion: {
          direction: 'minimize',
          target_metric_name: 'p1_misroutes',
          description: 'P1 tickets routed to the wrong specialist queue.',
        },
      },
    },
  },
}

export function getMock(wfId: string): WorkflowMock | null {
  return WORKFLOW_MOCKS[wfId] ?? null
}

export function isMock(wfId: string): boolean {
  return wfId in WORKFLOW_MOCKS
}

// Static failure-cluster fixtures for the positioning workflows.
// Same shape as the API's FailureClusterSummary so the mock and live
// codepaths render identical components. Keep counts small (2-3 per
// workflow) so the screenshot reads cleanly.
export interface MockFailureCluster {
  id: string
  workflow_id: string
  label: string
  severity: 'high' | 'medium' | 'low'
  cluster_size: number
  label_eval_score: number | null
  quality_score: number | null
  sample_trace_ids: string[]
  created_at: string
}

export const MOCK_FAILURE_CLUSTERS: Record<string, MockFailureCluster[]> = {
  labour: [
    {
      id: '00000000-0000-4000-8000-000000000001',
      workflow_id: 'labour',
      label: 'Late-shift swap not flagged for skill mismatch',
      severity: 'high',
      cluster_size: 8,
      label_eval_score: 0.82,
      quality_score: 0.71,
      sample_trace_ids: [],
      created_at: '2026-05-06T09:14:00Z',
    },
    {
      id: '00000000-0000-4000-8000-000000000002',
      workflow_id: 'labour',
      label: 'Overtime cap underestimated on holiday eligibility',
      severity: 'medium',
      cluster_size: 5,
      label_eval_score: 0.75,
      quality_score: 0.64,
      sample_trace_ids: [],
      created_at: '2026-05-04T16:22:00Z',
    },
  ],
  contract: [
    {
      id: '00000000-0000-4000-8000-000000000003',
      workflow_id: 'contract',
      label: 'Severance triggers for fixed-term renewals',
      severity: 'high',
      cluster_size: 4,
      label_eval_score: 0.79,
      quality_score: 0.68,
      sample_trace_ids: [],
      created_at: '2026-05-05T11:00:00Z',
    },
    {
      id: '00000000-0000-4000-8000-000000000004',
      workflow_id: 'contract',
      label: 'Ambiguous overtime clause across multi-state CBAs',
      severity: 'low',
      cluster_size: 3,
      label_eval_score: 0.72,
      quality_score: 0.55,
      sample_trace_ids: [],
      created_at: '2026-05-02T08:45:00Z',
    },
  ],
  support: [
    {
      id: '00000000-0000-4000-8000-000000000005',
      workflow_id: 'support',
      label: 'Refund requests with order-number typos',
      severity: 'medium',
      cluster_size: 11,
      label_eval_score: 0.84,
      quality_score: 0.73,
      sample_trace_ids: [],
      created_at: '2026-05-07T13:08:00Z',
    },
    {
      id: '00000000-0000-4000-8000-000000000006',
      workflow_id: 'support',
      label: 'Enterprise contract questions misrouted to billing',
      severity: 'low',
      cluster_size: 6,
      label_eval_score: 0.81,
      quality_score: 0.66,
      sample_trace_ids: [],
      created_at: '2026-05-03T19:34:00Z',
    },
  ],
}

export function getMockClusters(wfId: string): MockFailureCluster[] | null {
  return MOCK_FAILURE_CLUSTERS[wfId] ?? null
}
