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
    delta?: { direction: 'up' | 'down' | 'flat'; text: string }
  }>
  recentActivity: Array<{
    kind: 'approval' | 'cluster' | 'regression' | 'escalation'
    body: string
    when: string
  }>
}

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
      { label: 'Schedules / week', value: '482', delta: { direction: 'flat', text: 'unchanged' } },
      { label: 'Compliance breaches', value: '3', delta: { direction: 'up', text: '▼ 11 vs last week' } },
      { label: 'Approval rate', value: '94.1%', delta: { direction: 'up', text: '▲ 2.3 pts' } },
      { label: 'Cluster eval cases', value: '24', delta: { direction: 'flat', text: 'auto-promoted' } },
    ],
    recentActivity: [
      { kind: 'approval', body: 'Approved “Hard cap on weekend doubles in Pacific NW”', when: '2h ago' },
      { kind: 'cluster', body: 'New cluster surfaced: “Late-shift swap not flagged for skill mismatch”', when: '6h ago' },
      { kind: 'regression', body: 'Gate blocked v17.3: forecast_overtime regressed on 2 prior cases', when: '1d ago' },
    ],
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
      { label: 'Contracts / month', value: '38', delta: { direction: 'up', text: '▲ 6 vs last month' } },
      { label: 'Reviewer hours saved', value: '162h', delta: { direction: 'up', text: '▲ 28h' } },
      { label: 'False-flag rate', value: '4.7%', delta: { direction: 'down', text: '▲ 1.1 pts' } },
      { label: 'Cluster eval cases', value: '11', delta: { direction: 'flat', text: 'auto-promoted' } },
    ],
    recentActivity: [
      { kind: 'escalation', body: 'Counsel escalation: ambiguous overtime clause in Local 218', when: '4h ago' },
      { kind: 'approval', body: 'Approved “Recognize ‘pyramiding’ as discrete pay-stacking pattern”', when: '1d ago' },
      { kind: 'cluster', body: 'New cluster: “Severance triggers for fixed-term renewals”', when: '2d ago' },
    ],
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
      { label: 'Tickets / day', value: '1,820', delta: { direction: 'flat', text: 'unchanged' } },
      { label: 'P1 mis-routes', value: '7', delta: { direction: 'up', text: '▼ 14 vs last week' } },
      { label: 'Time-to-route p95', value: '38s', delta: { direction: 'up', text: '▼ 9s' } },
      { label: 'Cluster eval cases', value: '53', delta: { direction: 'flat', text: 'auto-promoted' } },
    ],
    recentActivity: [
      { kind: 'approval', body: 'Approved “Treat ‘billing dispute >30d’ as P1 by default”', when: '1h ago' },
      { kind: 'cluster', body: 'New cluster: “Refund requests with order-number typos”', when: '5h ago' },
      { kind: 'approval', body: 'Approved “Route enterprise contract questions to legal-cs”', when: '3d ago' },
    ],
  },
}

export function getMock(wfId: string): WorkflowMock | null {
  return WORKFLOW_MOCKS[wfId] ?? null
}

export function isMock(wfId: string): boolean {
  return wfId in WORKFLOW_MOCKS
}
