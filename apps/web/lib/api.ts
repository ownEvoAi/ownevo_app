// Server-side client for the kernel REST API.
//
// Every fetch in this file is meant to run on the server (in App Router
// Server Components / Server Actions). The client never holds a kernel
// URL — Next.js proxies via Server Actions for mutations and renders
// fetched data via Server Components for reads.
//
// Caching: list + detail fetches use `cache: 'no-store'` because the
// approval queue is intrinsically dynamic (an approval action mutates
// state; the next render must reflect it). For a polish pass with
// SSE streaming this swaps for revalidation tags.

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

export type ProposalState =
  | 'pending'
  | 'in-gate'
  | 'gate-failed'
  | 'gate-passed'
  | 'approved-awaiting-deploy'
  | 'deployed'
  | 'rejected'
  | 'rolled-back'

export interface ProposalSummary {
  id: string
  iteration_id: string
  iteration_index: number
  skill_id: string
  workflow_id: string
  workflow_description: string
  state: ProposalState
  plain_language_summary: string
  eval_score: number | null
  eval_rationale: string | null
  expected_impact: Record<string, unknown> | null
  created_at: string
  state_updated_at: string
}

export interface ProposalList {
  items: ProposalSummary[]
  total: number
}

export interface IterationDetail {
  id: string
  iteration_index: number
  state: string
  val_score: number | null
  best_ever_score_before: number | null
  best_ever_score_after: number | null
  sandbox_error_class: string | null
  started_at: string
  ended_at: string | null
}

export interface WorkflowDetail {
  id: string
  description: string
  mode: string
}

export interface AuditEntry {
  id: string
  seq: number
  kind: string
  actor: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ApprovalDetail {
  id: string
  decided_by: string
  approver_type: string
  decision: 'approve' | 'reject'
  comment: string | null
  became_eval_case_id: string | null
  decided_at: string
}

export interface ProposalDetail {
  id: string
  iteration_id: string
  skill_id: string
  parent_version_id: string | null
  state: ProposalState
  proposed_content: string
  parent_version_content: string | null
  parent_version_seq: number | null
  plain_language_summary: string
  eval_score: number | null
  eval_rationale: string | null
  expected_impact: Record<string, unknown> | null
  created_at: string
  state_updated_at: string
  iteration: IterationDetail
  workflow: WorkflowDetail
  audit_entries: AuditEntry[]
  approval: ApprovalDetail | null
  gate_result_cases: GateResultCases | null
}

export interface GateResultCases {
  passed: string[]
  regressed: string[]
  newly_admitted: string[]
  unknown: boolean
}

export interface ApproveResponse {
  proposal_id: string
  state: ProposalState
  approval: ApprovalDetail
}

export interface DecideRequest {
  decided_by: string
  comment?: string
  approver_type?: 'human' | 'llm-judge' | 'autonomous'
}

export class KernelApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(`kernel API ${status}: ${detail}`)
    this.status = status
    this.detail = detail
  }
}

// Structured banner helper: returns { title, detail } so the banner can
// render an accurate title. "Not reachable" only when the network failed;
// "error" when the kernel responded with an HTTP error code.
export function kernelError(err: unknown): { title: string; detail: string } {
  if (err instanceof KernelApiError) {
    return {
      title: 'Kernel API error.',
      detail: `${err.status}: ${err.detail}`,
    }
  }
  return {
    title: 'Kernel API not reachable.',
    detail: 'Could not reach the kernel API. Run `make api` to start it.',
  }
}

// String variant kept for server actions that return error as a plain string.
export function kernelErrorMessage(err: unknown): string {
  const { title, detail } = kernelError(err)
  return `${title} ${detail}`
}

async function jsonFetch<T>(
  path: string,
  init?: RequestInit & { revalidate?: number },
): Promise<T> {
  const url = `${API_URL}${path}`
  const res = await fetch(url, {
    cache: 'no-store',
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string | Array<{ msg?: string }> }
      if (Array.isArray(body?.detail)) {
        // FastAPI Pydantic validation errors return detail as an array of {loc, msg, type}.
        detail = body.detail.map((e) => e.msg ?? JSON.stringify(e)).join('; ')
      } else if (typeof body?.detail === 'string') {
        detail = body.detail
      }
    } catch {
      // Body wasn't JSON — keep the statusText fallback.
    }
    throw new KernelApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export async function listProposals(
  params: { state?: ProposalState; workflow_id?: string; limit?: number } = {},
): Promise<ProposalList> {
  const qs = new URLSearchParams()
  if (params.state) qs.set('state', params.state)
  if (params.workflow_id) qs.set('workflow_id', params.workflow_id)
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  const path = qs.toString() ? `/api/proposals?${qs}` : '/api/proposals'
  return jsonFetch<ProposalList>(path)
}

export async function getProposal(id: string): Promise<ProposalDetail> {
  return jsonFetch<ProposalDetail>(`/api/proposals/${id}`)
}

export async function approveProposal(
  id: string,
  body: DecideRequest,
): Promise<ApproveResponse> {
  return jsonFetch<ApproveResponse>(`/api/proposals/${id}/approve`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function rejectProposal(
  id: string,
  body: DecideRequest,
): Promise<ApproveResponse> {
  return jsonFetch<ApproveResponse>(`/api/proposals/${id}/reject`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// W5.5 — NL-gen preview surface

export type DimensionVerdict = 'pass' | 'partial' | 'fail'
export type OverallVerdict = 'good' | 'bad'

export interface MetaEvalDimension {
  verdict: DimensionVerdict
  rationale: string
}

export interface MetaEvalJudgment {
  schema_version: '0.1'
  workflow_spec_id: string
  sim_coverage: MetaEvalDimension
  eval_case_coverage: MetaEvalDimension
  metric_alignment: MetaEvalDimension
  overall_verdict: OverallVerdict
  overall_rationale: string
}

export interface PreviewResponse {
  workflow_id: string
  description: string
  workflow_spec: Record<string, unknown>
  simulation_plan: Record<string, unknown>
  eval_case_set: Record<string, unknown>
  metric_definition: Record<string, unknown>
  meta_eval_judgment: MetaEvalJudgment
  provenance: 'preview-fixture'
}

export interface PreviewIndexEntry {
  workflow_id: string
  description: string
}

export interface PreviewIndex {
  items: PreviewIndexEntry[]
}

export async function listPreviewWorkflows(): Promise<PreviewIndex> {
  return jsonFetch<PreviewIndex>('/api/nl-gen/preview')
}

export async function getPreview(workflowId: string): Promise<PreviewResponse> {
  return jsonFetch<PreviewResponse>(
    `/api/nl-gen/preview/${encodeURIComponent(workflowId)}`,
  )
}

export interface GenerateWorkflowResponse {
  workflow_id: string
  description: string
  spec: WorkflowSpecShape
}

export async function generateWorkflow(
  description: string,
  workflowId?: string,
): Promise<GenerateWorkflowResponse> {
  const body: Record<string, unknown> = { description }
  if (workflowId) body.workflow_id = workflowId
  return jsonFetch<GenerateWorkflowResponse>('/api/nl-gen/generate', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export interface EvalCaseProvenance {
  /** 'derived' = verbatim user-flagged miss; 'inferred' = named pattern. */
  kind: string
  source: string
}

export interface EvalCaseSummary {
  id: string
  case_id: string
  provenance: string
  rationale: string | null
  target_label_field: string | null
  expected_value: unknown
  sim_seed: number | null
  n_steps: number | null
  target_step_index: number | null
  is_test_fold: boolean
  cluster_id: string | null
  created_at: string
  /** `{kind, source}` from `expected_behavior.provenance`; null for
   * hand-authored cases and any row that pre-dates the convention. */
  expected_behavior_provenance: EvalCaseProvenance | null
  /** Coarse bucket derived server-side from provenance.kind:
   * 'past-miss' (derived) | 'inferred' (inferred) | null. */
  category: string | null
}

export interface EvalCaseList {
  workflow_id: string
  items: EvalCaseSummary[]
  total: number
}

export async function listWorkflowEvalCases(
  workflowId: string,
): Promise<EvalCaseList> {
  return jsonFetch<EvalCaseList>(
    `/api/workflows/${encodeURIComponent(workflowId)}/eval-cases`,
  )
}

// PLAN 8.4.9 (Phase A) — per-case agent output. PLAN 8.4.10 (Phase B)
// wires this to the operator-shell TableView primitive.
export interface CaseOutputRow {
  eval_case_id: string
  case_id: string | null
  output_json: Record<string, unknown>
  input: Record<string, unknown>
  expected_behavior: Record<string, unknown>
  passed: boolean
  is_test_fold: boolean
  created_at: string
  trace_id: string | null
}

export interface CaseOutputList {
  workflow_id: string
  iteration_index: number | null
  iteration_id: string | null
  items: CaseOutputRow[]
}

export async function getWorkflowCaseOutputs(
  workflowId: string,
  options: { iteration?: number | 'latest' } = {},
): Promise<CaseOutputList> {
  const iter = options.iteration ?? 'latest'
  return jsonFetch<CaseOutputList>(
    `/api/workflows/${encodeURIComponent(workflowId)}/case-outputs?iteration=${encodeURIComponent(String(iter))}`,
  )
}

export interface GenerateEvalCasesResponse {
  workflow_id: string
  generated: number
  train_count: number
  test_count: number
}

export async function generateEvalCases(
  workflowId: string,
): Promise<GenerateEvalCasesResponse> {
  return jsonFetch<GenerateEvalCasesResponse>(
    `/api/workflows/${encodeURIComponent(workflowId)}/eval-cases/generate`,
    { method: 'POST', body: '{}' },
  )
}

export interface RunIterationResponse {
  iteration_id: string
  iteration_index: number
  state: string
  val_score: number
  n_cases: number
  n_failed: number
  proposed_skill_id: string | null
  proposed_skill_version_id: string | null
  proposed_instruction: string | null
  proposal_id: string | null
}

export async function runWorkflowIteration(
  workflowId: string,
): Promise<RunIterationResponse> {
  return jsonFetch<RunIterationResponse>(
    `/api/workflows/${encodeURIComponent(workflowId)}/iterations/run`,
    { method: 'POST', body: '{}' },
  )
}

export interface IterationCaseRow {
  case_id: string
  predicted: boolean | null
  expected: boolean | null
  passed: boolean | null
  is_test_fold: boolean
  rationale: string | null
  trace_id: string
  started_at: string
  ended_at: string | null
}

export interface IterationDetail {
  workflow_id: string
  iteration_id: string
  iteration_index: number
  state: string
  val_score: number | null
  best_ever_score_before: number | null
  best_ever_score_after: number | null
  n_cases: number
  n_passed: number
  n_failed: number
  cluster_id: string | null
  cluster_label: string | null
  parent_skill_version_id: string | null
  proposed_skill_version_id: string | null
  proposal_id: string | null
  started_at: string
  ended_at: string | null
  cases: IterationCaseRow[]
}

export async function getIterationDetail(
  workflowId: string,
  iterationIndex: number,
): Promise<IterationDetail> {
  return jsonFetch<IterationDetail>(
    `/api/workflows/${encodeURIComponent(workflowId)}/iterations/${iterationIndex}`,
  )
}

export interface WorkflowDeleteResponse {
  id: string
  iterations: number
  proposals: number
  approvals: number
  traces: number
  eval_cases: number
  failure_clusters: number
  learnings: number
  skill_versions: number
  skills: number
  meta_evals: number
}

export async function updateWorkflowDescription(
  workflowId: string,
  description: string,
): Promise<WorkflowAnatomy> {
  return jsonFetch<WorkflowAnatomy>(
    `/api/workflows/${encodeURIComponent(workflowId)}`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ description }),
    },
  )
}

export async function deleteWorkflow(
  workflowId: string,
): Promise<WorkflowDeleteResponse> {
  return jsonFetch<WorkflowDeleteResponse>(
    `/api/workflows/${encodeURIComponent(workflowId)}`,
    { method: 'DELETE' },
  )
}

// W7 slice 2 — workspace Health page + LiftChart

export interface WorkflowSummary {
  id: string
  description: string
  mode: string
  /** 'benchmark' tags M5/tau-bench rows; null/absent = production. */
  kind?: string | null
  iteration_count: number
  running_iteration_count?: number
  oldest_running_started_at?: string | null
  best_ever_score: number | null
  last_improved_at: string | null
  pending_proposals_count: number
}

export interface WorkflowList {
  items: WorkflowSummary[]
  total: number
}

export interface IterationPoint {
  iteration_index: number
  val_score: number | null
  best_ever_score_after: number | null
  state: string
  has_approved_proposal: boolean
  ended_at: string | null
}

export interface IterationList {
  workflow_id: string
  items: IterationPoint[]
}

export async function listWorkflows(): Promise<WorkflowList> {
  return jsonFetch<WorkflowList>('/api/workflows')
}

// W7 slice 11 (7.1.12) — Workflow anatomy (full spec for the
// Agent-anatomy pane on the workflow Overview page).

export interface AgentToolParam {
  name: string
  type: string
  description?: string
  required?: boolean
}

/** `{kind, source}` shape NL-gen attaches to most spec sub-items.
 * `kind="derived"` → `source` is a verbatim phrase from the user's
 * description. `kind="inferred"` → `source` names a domain pattern. */
export interface SpecProvenance {
  kind: string
  source: string
}

export interface AgentToolSpec {
  name: string
  description?: string
  inputs?: AgentToolParam[]
  outputs?: AgentToolParam[]
  provenance?: SpecProvenance | null
}

export interface ReviewerSpec {
  role: string
  cadence?: string
  description?: string
  provenance?: SpecProvenance | null
}

export interface DataSourceSpec {
  id: string
  description?: string
  entity?: string | null
  provenance?: SpecProvenance | null
}

export interface EnvGeneratorSpec {
  name: string
  description?: string
  provenance?: SpecProvenance | null
}

export interface PersonaSpec {
  role: string
  name?: string | null
  cadence?: string
  description?: string
  provenance?: SpecProvenance | null
}

export interface WorkflowEnvironmentSpec {
  entities?: Array<{ name: string; description?: string }>
  data_sources?: DataSourceSpec[]
  env_generators?: EnvGeneratorSpec[]
  personas?: PersonaSpec[]
  seasonality?: string[]
}

export interface WorkflowUITab {
  name?: string
  primitives?: Array<{ type: string; [key: string]: unknown }>
}

export interface WorkflowUILayout {
  layout?: string
  tabs?: WorkflowUITab[]
}

export interface WorkflowSpecShape {
  domain?: string
  environment?: WorkflowEnvironmentSpec
  tools?: AgentToolSpec[]
  reviewer?: ReviewerSpec
  success_criterion?: {
    direction?: 'maximize' | 'minimize'
    target_metric_name?: string
    description?: string
  }
  ui?: WorkflowUILayout
  [key: string]: unknown
}

/** Loose shape — the kernel schema is `SimulationPlan` in
 * `nl_gen/sim_plan.py`. The review page reads `description` +
 * `n_steps_default`; everything else is left opaque so spec-version
 * bumps don't force a TS revision. */
export interface SimulationPlanShape {
  description?: string
  n_steps_default?: number
  seed_default?: number
  [key: string]: unknown
}

/** Loose shape — kernel schema is `MetricDefinition` in
 * `nl_gen/metric_def.py`. Review page reads name + family + direction +
 * description + provenance.{kind,source} for the "derived from <phrase>"
 * caption. `[key: string]: unknown` covers bounds / target_value /
 * rationale without typing them. */
export interface MetricDefinitionShape {
  name?: string
  family?: string
  direction?: string
  description?: string
  rationale?: string
  provenance?: SpecProvenance
  [key: string]: unknown
}

export interface WorkflowAnatomy {
  id: string
  description: string
  mode: string
  /** 'benchmark' tags M5/tau-bench rows; null/absent = production. */
  kind?: string | null
  spec: WorkflowSpecShape
  /** Persisted SimulationPlan JSONB; null when NL-gen hasn't run. */
  simulation_plan?: SimulationPlanShape | null
  /** Persisted MetricDefinition JSONB; null when NL-gen hasn't run. */
  metric_definition?: MetricDefinitionShape | null
}

export async function getWorkflowAnatomy(
  workflowId: string,
): Promise<WorkflowAnatomy> {
  return jsonFetch<WorkflowAnatomy>(
    `/api/workflows/${encodeURIComponent(workflowId)}`,
  )
}

export async function getWorkflowIterations(
  workflowId: string,
): Promise<IterationList> {
  return jsonFetch<IterationList>(
    `/api/workflows/${encodeURIComponent(workflowId)}/iterations`,
  )
}

// W7 slice 3 — Failure clusters

export type ClusterSeverity = 'high' | 'medium' | 'low'

export interface FailureClusterSummary {
  id: string
  workflow_id: string | null
  label: string
  severity: ClusterSeverity
  cluster_size: number
  label_eval_score: number | null
  quality_score: number | null
  sample_trace_ids: string[]
  created_at: string
  latest_proposal_id: string | null
  spawning_iteration_index?: number | null
  spawning_iteration_id?: string | null
}

export interface FailureClusterList {
  workflow_id: string
  items: FailureClusterSummary[]
}

export async function getWorkflowFailureClusters(
  workflowId: string,
): Promise<FailureClusterList> {
  return jsonFetch<FailureClusterList>(
    `/api/workflows/${encodeURIComponent(workflowId)}/failure_clusters`,
  )
}

// W7 slice 4 — Audit trail + verify-chain

export interface AuditEntryRow {
  id: string
  seq: number
  kind: string
  actor: string
  related_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface AuditList {
  items: AuditEntryRow[]
  total: number
  truncated: boolean
}

export interface AuditVerifyResponse {
  valid: boolean
  total_entries: number
  min_seq: number | null
  max_seq: number | null
  missing_seqs: number[]
  duplicate_seqs: number[]
  canonical_export_bytes: number
  checked_at: string
}

export async function listAudit(
  params: {
    kind?: string
    sinceSeq?: number
    limit?: number
    workflowId?: string
  } = {},
): Promise<AuditList> {
  const qs = new URLSearchParams()
  if (params.kind) qs.set('kind', params.kind)
  if (params.sinceSeq !== undefined) qs.set('since_seq', String(params.sinceSeq))
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  if (params.workflowId) qs.set('workflow_id', params.workflowId)
  const path = qs.toString() ? `/api/audit?${qs}` : '/api/audit'
  return jsonFetch<AuditList>(path)
}

export async function verifyAuditChain(): Promise<AuditVerifyResponse> {
  return jsonFetch<AuditVerifyResponse>('/api/audit/verify', { method: 'POST' })
}

// W7 slice 8 (7.1.9) — Traces

export interface TraceSummary {
  id: string
  workflow_id: string | null
  iteration_id: string | null
  iteration_index: number | null
  skill_version_id: string | null
  started_at: string
  ended_at: string | null
  event_count: number
  kind_counts: Record<string, number>
}

export interface TraceList {
  workflow_id: string
  items: TraceSummary[]
}

// AgentEvent variants — discriminated by `type`. Matches
// packages/trace-format/SPEC.md v1.0. Fields shared via AgentEventBase.
export interface AgentEventBase {
  event_id: string
  trace_id: string
  iteration_id: string | null
  timestamp: string
  parent_span_id: string | null
}

export interface ContentDelta extends AgentEventBase {
  type: 'content_delta'
  text: string
  model: string
  cumulative_text: string | null
}

export interface ReasoningDelta extends AgentEventBase {
  type: 'reasoning_delta'
  text: string
  model: string
}

export interface ToolCallStart extends AgentEventBase {
  type: 'tool_call_start'
  call_id: string
  name: string
  args: Record<string, unknown>
}

export interface ToolCallResult extends AgentEventBase {
  type: 'tool_call_result'
  call_id: string
  name: string
  status: 'ok' | 'error'
  output: unknown
  duration_ms: number
  error: string | null
  error_class: 'Timeout' | 'OOM' | 'Crash' | null
}

export interface SkillLoaded extends AgentEventBase {
  type: 'skill_loaded'
  skill_id: string
  version_seq: number
  retention_acknowledged: boolean
}

export interface CitationEvent extends AgentEventBase {
  type: 'citation'
  ref: number
  source: string
  quote: string
}

export interface MonitorSignal extends AgentEventBase {
  type: 'monitor_signal'
  monitor: 'loop_detection' | 'redundancy' | 'context_near_limit'
  severity: 'info' | 'warn' | 'error'
  details: Record<string, unknown> | null
}

export type AgentEvent =
  | ContentDelta
  | ReasoningDelta
  | ToolCallStart
  | ToolCallResult
  | SkillLoaded
  | CitationEvent
  | MonitorSignal

export interface TraceDetail {
  id: string
  workflow_id: string | null
  iteration_id: string | null
  iteration_index: number | null
  skill_version_id: string | null
  skill_id: string | null
  skill_version_seq: number | null
  started_at: string
  ended_at: string | null
  metric_outputs: Record<string, unknown> | null
  token_usage: Record<string, unknown> | null
  events: AgentEvent[]
}

export interface EvalCaseCreatePayload {
  case_id: string
  expected_value: boolean
  target_label_field: string
  rationale?: string
  is_test_fold?: boolean
  sim_seed?: number
  n_steps?: number
  target_step_index?: number
}

export async function createEvalCase(
  workflowId: string,
  payload: EvalCaseCreatePayload,
): Promise<EvalCaseSummary> {
  return jsonFetch<EvalCaseSummary>(
    `/api/workflows/${encodeURIComponent(workflowId)}/eval-cases`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )
}

export async function deleteEvalCase(
  workflowId: string,
  caseId: string,
): Promise<void> {
  // Custom fetch — the endpoint returns 204 No Content; jsonFetch would
  // choke trying to parse an empty body.
  const url = `${API_URL}/api/workflows/${encodeURIComponent(workflowId)}/eval-cases/${encodeURIComponent(caseId)}`
  const res = await fetch(url, { method: 'DELETE', cache: 'no-store' })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // body wasn't JSON
    }
    throw new KernelApiError(res.status, detail)
  }
}

export async function listAllTraces(): Promise<TraceList> {
  return jsonFetch<TraceList>(`/api/traces`)
}

export async function getWorkflowTraces(
  workflowId: string,
): Promise<TraceList> {
  return jsonFetch<TraceList>(
    `/api/workflows/${encodeURIComponent(workflowId)}/traces`,
  )
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  return jsonFetch<TraceDetail>(`/api/traces/${encodeURIComponent(traceId)}`)
}

// W7 slices 9 + 10 (7.1.10 + 7.1.11) — Skills

export type SkillKind = 'python' | 'instruction' | 'composite'

export interface SkillSummary {
  id: string
  kind: SkillKind
  workflow_id: string | null
  capability_tags: string[]
  head_version_id: string | null
  head_version_seq: number | null
  head_created_at: string | null
}

export interface SkillList {
  items: SkillSummary[]
}

export interface SkillVersionSummary {
  id: string
  version_seq: number
  parent_version_id: string | null
  diff_summary: string | null
  created_by: string
  created_at: string
}

export interface SkillRelatedEvalCase {
  id: string
  workflow_id: string | null
  provenance: string
  expected_behavior: Record<string, unknown> | null
  is_test_fold: boolean
  created_at: string
}

export interface SkillDetail {
  id: string
  kind: SkillKind
  workflow_id: string | null
  workflow_description: string | null
  capability_tags: string[]
  head_version_id: string | null
  head_version_seq: number | null
  head_content: string | null
  head_retention_block: Record<string, unknown> | null
  head_diff_summary: string | null
  head_created_at: string | null
  head_created_by: string | null
  parent_content: string | null
  parent_version_seq: number | null
  deployed_version_id: string | null
  deployed_version_seq: number | null
  deployable_proposal_id: string | null
  deployable_proposal_version_seq: number | null
  deployed_proposal_id: string | null
  versions: SkillVersionSummary[]
  related_eval_cases: SkillRelatedEvalCase[]
}

export interface DeployRequest {
  decided_by: string
}

export interface DeployResponse {
  proposal_id: string
  state: ProposalState
  skill_id: string
  skill_deployed_version_id: string | null
}

export async function deployProposal(
  id: string,
  body: DeployRequest,
): Promise<DeployResponse> {
  return jsonFetch<DeployResponse>(`/api/proposals/${id}/deploy`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function rollbackProposal(
  id: string,
  body: DeployRequest,
): Promise<DeployResponse> {
  return jsonFetch<DeployResponse>(`/api/proposals/${id}/rollback`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function getWorkflowSkills(
  workflowId: string,
): Promise<SkillList> {
  return jsonFetch<SkillList>(
    `/api/workflows/${encodeURIComponent(workflowId)}/skills`,
  )
}

// Workspace-scoped Skills library index (PLAN 8.0.4). Single-tenant
// for MVP per D4 — every skill in the DB is the "workspace".
export async function listSkills(): Promise<SkillList> {
  return jsonFetch<SkillList>('/api/skills')
}

export async function getSkill(skillId: string): Promise<SkillDetail> {
  return jsonFetch<SkillDetail>(`/api/skills/${encodeURIComponent(skillId)}`)
}
