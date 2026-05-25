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

import { cache } from 'react'

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
  | 'changes-requested'

export type ProposalKind =
  | 'skill'
  | 'description'
  | 'metric'
  | 'sim'
  | 'ui-primitive'

export interface ProposalSummary {
  id: string
  iteration_id: string
  iteration_index: number
  skill_id: string | null
  kind: ProposalKind
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
  decision: 'approve' | 'reject' | 'request-changes'
  comment: string | null
  became_eval_case_id: string | null
  decided_at: string
}

export interface ProposalDetail {
  id: string
  iteration_id: string
  skill_id: string | null
  kind: ProposalKind
  parent_version_id: string | null
  state: ProposalState
  proposed_content: string
  proposed_payload: Record<string, unknown> | null
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
  // 204 No Content — no body to parse.
  if (res.status === 204) return undefined as T
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

export async function requestChangesProposal(
  id: string,
  body: DecideRequest,
): Promise<ApproveResponse> {
  return jsonFetch<ApproveResponse>(`/api/proposals/${id}/request-changes`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// 9.2.3 — create a kind='metric' proposal on a workflow. The new
// metric definition lands in `proposed_payload` and the proposal
// is anchored to the workflow's latest iteration.
export interface CreateMetricProposalBody {
  plain_language_summary: string
  proposed_metric: Record<string, unknown>
  rationale?: string | null
}

export async function createMetricProposal(
  workflowId: string,
  body: CreateMetricProposalBody,
): Promise<ProposalSummary> {
  return jsonFetch<ProposalSummary>(
    `/api/workflows/${encodeURIComponent(workflowId)}/proposals/metric`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

// 9.2.3 — create a kind='sim' proposal. `proposed_spec` is a partial
// WorkflowSpec carrying any of: tools / personas / data_sources /
// env_generators. At least one of those keys is required.
export interface CreateSimProposalBody {
  plain_language_summary: string
  proposed_spec: Record<string, unknown>
  rationale?: string | null
}

export async function createSimProposal(
  workflowId: string,
  body: CreateSimProposalBody,
): Promise<ProposalSummary> {
  return jsonFetch<ProposalSummary>(
    `/api/workflows/${encodeURIComponent(workflowId)}/proposals/sim`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

// 9.2.3 — create a kind='description' proposal. Separate from the
// direct PATCH used by the inline description-edit: that path is for
// cosmetic "quick edit"; this is the gate-routed path for
// substantive rewrites.
export interface CreateDescriptionProposalBody {
  plain_language_summary: string
  proposed_description: string
  rationale?: string | null
}

export async function createDescriptionProposal(
  workflowId: string,
  body: CreateDescriptionProposalBody,
): Promise<ProposalSummary> {
  return jsonFetch<ProposalSummary>(
    `/api/workflows/${encodeURIComponent(workflowId)}/proposals/description`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

// 9.2.3 — create a kind='ui-primitive' proposal. `proposed_primitives`
// is the new operate-tab primitive list; each entry must carry `type`.
export interface CreateUIPrimitiveProposalBody {
  plain_language_summary: string
  proposed_primitives: Array<{ type: string; [k: string]: unknown }>
  rationale?: string | null
}

export async function createUIPrimitiveProposal(
  workflowId: string,
  body: CreateUIPrimitiveProposalBody,
): Promise<ProposalSummary> {
  return jsonFetch<ProposalSummary>(
    `/api/workflows/${encodeURIComponent(workflowId)}/proposals/ui-primitive`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

// 9.2.3 — ordering-inversion check for kind='metric' proposals.
// Returned shape mirrors `proposals.ordering_inversion.to_api_dict`.
export interface InversionIterationDelta {
  iteration_index: number
  old_score: number | null
  new_score: number | null
  delta: number | null
  old_meets_target: boolean | null
  new_meets_target: boolean | null
  inverted: boolean
  n_cases: number
}

export interface OrderingInversionCheck {
  status: 'ok' | 'unavailable' | 'error'
  reason: string | null
  current_metric_family: string | null
  proposed_metric_family: string | null
  n_inverted: number
  iterations: InversionIterationDelta[]
}

export async function getOrderingInversionCheck(
  proposalId: string,
): Promise<OrderingInversionCheck> {
  return jsonFetch<OrderingInversionCheck>(
    `/api/proposals/${proposalId}/ordering-inversion-check`,
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
  templateId?: string,
  designAgentLog?: DesignAgentLog | null,
  cookieHeader?: string,
): Promise<GenerateWorkflowResponse> {
  const body: Record<string, unknown> = { description }
  if (workflowId) body.workflow_id = workflowId
  if (templateId) body.template_id = templateId
  if (designAgentLog) body.design_agent_log = designAgentLog
  return jsonFetch<GenerateWorkflowResponse>('/api/nl-gen/generate', {
    method: 'POST',
    body: JSON.stringify(body),
    headers: cookieHeader ? { cookie: cookieHeader } : undefined,
  })
}

// ---- Design-agent discovery (Track 9.1) ---------------------------------

export type DiscoveryQuestionKind =
  | 'metric'
  | 'ambiguity'
  | 'trigger'
  | 'surface'
  | 'premise'

// The seven design-shaping dimensions the LLM interviewer covers.
// Mirrors `DesignDimension` in apps/kernel/.../design_agent/dimensions.py.
export type DesignDimension =
  | 'goal_and_scope'
  | 'trigger_and_cadence'
  | 'data_sources_and_connectors'
  | 'success_metric'
  | 'eval_seed_cases'
  | 'operate_ui_primitives'
  | 'reviewer_role'

export interface DiscoveryOption {
  label: string
  pro: string
  con: string
}

export interface NextDiscoveryQuestion {
  dimension: DesignDimension
  source: 'llm' | 'fallback'
  question: string
  eli: string
  stakes: string
  options: DiscoveryOption[]
  // Transitional: flat label list for code that expected string[].
  // Remove once all consumers use options[].label.
  options_labels?: string[]
  recommendation_index: number
  rationale: string
  // Legacy fields retained for compatibility with the existing
  // fallback-path renderer.
  question_index?: number
  kind?: DiscoveryQuestionKind | null
}

export interface PriorDiscoveryAnswer {
  // New shape: dimension-scoped answer carrying the chosen option +
  // optional elaboration. Legacy `question_index` + `answer` are still
  // accepted by the kernel for back-compat.
  dimension?: DesignDimension | null
  question?: string
  chosen_option?: string | null
  free_text?: string | null
  // Legacy fields (kept until the web client fully migrates).
  question_index?: number | null
  answer?: string | null
}

export interface NextDiscoveryQuestionResponse {
  next_question: NextDiscoveryQuestion | null
  done: boolean
  total_questions: number
  answered_count: number
}

// Structured persistence of the authoring-time discovery conversation.
// Mirrors the kernel `DesignAgentLog` pydantic shape so the web layer
// can post it as the structured `design_agent_log` field on POST
// /api/nl-gen/generate. Server persists to the JSONB column + mirrors
// per-entry rows into `audit_entries`. The four new optional fields
// (dimension, chosen_option) feed `nl_gen.design_brief_context` so
// each generator reads the dimensions it can actually encode.
export interface DesignAgentLogEntry {
  question_index: number
  kind: DiscoveryQuestionKind
  question: string
  answer: string | null
  dimension?: DesignDimension | null
  chosen_option?: string | null
}

export type AmbiguityFindingKind = 'inferred-artifact' | 'conflict'
export type AmbiguitySeverity = 'low' | 'medium' | 'high'

export interface AmbiguityFinding {
  kind: AmbiguityFindingKind
  severity: AmbiguitySeverity
  location: string
  summary: string
  suggested_question: string
}

export interface AmbiguityReport {
  workflow_spec_id: string
  findings: AmbiguityFinding[]
  high_severity_count: number
}

export interface DesignAgentLog {
  discovery_transcript: DesignAgentLogEntry[]
  ambiguity_report: AmbiguityReport | null
}

export async function fetchNextDiscoveryQuestion(
  description: string,
  templateId: string | null,
  priorAnswers: PriorDiscoveryAnswer[],
  signal?: AbortSignal,
  cookieHeader?: string,
): Promise<NextDiscoveryQuestionResponse> {
  return jsonFetch<NextDiscoveryQuestionResponse>('/api/design-agent/next-question', {
    method: 'POST',
    body: JSON.stringify({
      description,
      template_id: templateId,
      prior_answers: priorAnswers,
    }),
    headers: cookieHeader ? { cookie: cookieHeader } : undefined,
    signal,
  })
}

export interface DescriptionConflictsResponse {
  findings: AmbiguityFinding[]
}

// Pre-generation conflict scan. Distinct from `/ambiguity-report`: that
// endpoint needs a WorkflowSpec and runs after `/generate`; this one
// runs over the raw description so the chat panel can surface
// contradictions ("maximize recall, zero false positives") as additional
// questions before the operator clicks Generate. Returns bare findings
// (no `workflow_spec_id`, no `high_severity_count`) — the operator's
// answers ride into `discovery_transcript` alongside the static
// discovery Q/A entries.
export async function fetchDescriptionConflicts(
  description: string,
  signal?: AbortSignal,
): Promise<DescriptionConflictsResponse> {
  return jsonFetch<DescriptionConflictsResponse>(
    '/api/design-agent/description-conflicts',
    {
      method: 'POST',
      body: JSON.stringify({ description }),
      signal,
    },
  )
}

// Trace-import design surface. The discovery interview
// runs over imported agent traces instead of a written description; the
// kernel's LLM interviewer reads a summary of the traces (+ optional
// exported agent definition) and returns the same NextDiscoveryQuestion
// brief shape the authoring surface uses.
export async function fetchImportNextQuestion(
  traceIds: string[],
  agentDefinition: string | null,
  priorAnswers: PriorDiscoveryAnswer[],
  signal?: AbortSignal,
  cookieHeader?: string,
): Promise<NextDiscoveryQuestionResponse> {
  return jsonFetch<NextDiscoveryQuestionResponse>(
    '/api/design-agent/import-next-question',
    {
      method: 'POST',
      body: JSON.stringify({
        trace_ids: traceIds,
        agent_definition: agentDefinition,
        prior_answers: priorAnswers,
      }),
      headers: cookieHeader ? { cookie: cookieHeader } : undefined,
      signal,
    },
  )
}

export interface ImportSummaryResponse {
  /** One-to-two-sentence "this agent appears to do X" inference. */
  summary: string
  /** What evidence the summary was drawn from. */
  basis: 'traces' | 'definition+traces'
  /** Whether the summary was LLM-generated or rendered deterministically. */
  source: 'llm' | 'fallback'
  /** True when the supplied agent_definition was truncated before being passed to the LLM. */
  agent_definition_truncated?: boolean
}

// Open the trace-import conversation with a reverse-discovery summary the
// reviewer confirms or corrects before the dimension interview begins.
export async function fetchImportSummary(
  traceIds: string[],
  agentDefinition: string | null,
  signal?: AbortSignal,
  cookieHeader?: string,
): Promise<ImportSummaryResponse> {
  return jsonFetch<ImportSummaryResponse>(
    '/api/design-agent/import-summary',
    {
      method: 'POST',
      body: JSON.stringify({
        trace_ids: traceIds,
        agent_definition: agentDefinition,
      }),
      headers: cookieHeader ? { cookie: cookieHeader } : undefined,
      signal,
    },
  )
}

export interface ImportGenerateResponse {
  workflow_id: string
  description: string
  spec: Record<string, unknown>
}

// The reverse-discovery turn + the reviewer's decision, echoed back at
// generate time so the kernel can persist it to the import audit log.
export interface ReverseDiscoveryInput {
  inferred_summary: string
  basis: 'traces' | 'definition+traces'
  source: 'llm' | 'fallback'
  decision: 'confirmed' | 'corrected' | 'skipped'
  final_definition: string | null
}

// Reverse-engineer a WorkflowSpec from imported traces + the negotiated
// discovery answers, persist the workflow, and mirror the reverse-discovery
// turn + transcript into the audit chain. Returns the new workflow id.
// Vendor the imported agent came from; tags the created workflow's origin
// so the right fix-delivery action appears later. null = greenfield.
export type WorkflowOrigin = 'langsmith' | 'copilot_studio'

export async function generateFromImport(
  traceIds: string[],
  agentDefinition: string | null,
  designAgentLog: DesignAgentLog | null,
  reverseDiscovery: ReverseDiscoveryInput | null,
  origin?: WorkflowOrigin | null,
  workflowId?: string,
  cookieHeader?: string,
): Promise<ImportGenerateResponse> {
  return jsonFetch<ImportGenerateResponse>(
    '/api/design-agent/import-generate',
    {
      method: 'POST',
      body: JSON.stringify({
        trace_ids: traceIds,
        agent_definition: agentDefinition,
        reverse_discovery: reverseDiscovery,
        design_agent_log: designAgentLog,
        origin: origin ?? null,
        workflow_id: workflowId,
      }),
      headers: cookieHeader ? { cookie: cookieHeader } : undefined,
    },
  )
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
  // Domain-shaped output the agent emitted for this case (forecast
  // curve, redline pair, recommendation table). Null when the agent
  // didn't emit one. The Operate-tab resolver reads this and dispatches
  // to the workflow's declared primitives.
  output_payload: Record<string, unknown> | null
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

// ─── Try-it (PLAN 8.5.2) ─────────────────────────────────────────────

export interface TryItRequest {
  eval_case_id?: string
  free_form_input?: string
  /** Optional model override. Defaults to the kernel's DEFAULT_MODEL
   * (claude-haiku-4-5 today). Local LLM ids accepted; cost falls
   * through to 0 for unknown models. */
  model?: string
}

/** One AgentEvent on the wire — minimal shape the Try-it trace pair
 * actually populates. Matches packages/trace-format/AgentEvent for the
 * `tool_call_start` and `tool_call_result` variants. */
export interface TryItTraceEvent {
  type: 'tool_call_start' | 'tool_call_result' | string
  event_id: string
  trace_id: string
  iteration_id: string | null
  timestamp: string
  call_id?: string
  name?: string
  args?: Record<string, unknown>
  status?: 'ok' | 'error'
  duration_ms?: number
  output?: Record<string, unknown>
  error?: string | null
  error_class?: string | null
}

export interface TryItResponse {
  case_id: string
  expected_value: unknown
  actual_value: unknown
  rationale: string
  passed: boolean
  model: string
  duration_ms: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  trace: TryItTraceEvent[]
}

export async function tryWorkflow(
  workflowId: string,
  body: TryItRequest,
): Promise<TryItResponse> {
  return jsonFetch<TryItResponse>(
    `/api/workflows/${encodeURIComponent(workflowId)}/try`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
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
  /** Vertical-template id the workflow was created from; null for free-form
   * authoring. Matches an entry in `app/.../workflows/new/templates.ts`. */
  created_from_template: string | null
  /** Design-agent discovery transcript + ambiguity report; null when the
   * workflow was authored without running the discovery interview. */
  design_agent_log: DesignAgentLog | null
  /** Provider-prefixed agent-model slug, e.g. 'anthropic:claude-sonnet-4-6'.
   * Defaults to 'anthropic:claude-sonnet-4-6' on rows that pre-date migration
   * 0014 / never went through the picker. Validated against the runtime
   * `OWNEVO_PROVIDER_*` env allowlist on PATCH. */
  agent_model_id: string
  /** Vendor the workflow was imported from ('langsmith' | 'copilot_studio'),
   * or null for greenfield workflows. Gates origin-specific UI actions. */
  origin: WorkflowOrigin | null
}

/** Single provider entry in the `/api/models` response — one `<optgroup>`. */
export interface ProviderModels {
  id: string
  label: string
  models: string[]
}

export interface ModelCatalog {
  providers: ProviderModels[]
}

export async function getModelCatalog(): Promise<ModelCatalog> {
  return jsonFetch<ModelCatalog>('/api/models')
}

export async function updateWorkflowAgentModel(
  workflowId: string,
  agentModelId: string,
): Promise<WorkflowAnatomy> {
  return jsonFetch<WorkflowAnatomy>(
    `/api/workflows/${encodeURIComponent(workflowId)}/agent-model`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ agent_model_id: agentModelId }),
    },
  )
}

// cache() deduplicates calls with the same workflowId within one render pass.
// Both the workflow layout and the audit page call this; only one kernel
// request fires per page load.
export const getWorkflowAnatomy = cache(async (
  workflowId: string,
): Promise<WorkflowAnatomy> => {
  return jsonFetch<WorkflowAnatomy>(
    `/api/workflows/${encodeURIComponent(workflowId)}`,
  )
})

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
  // 9.2.1 — per-cluster source mix derived from traces.iteration_id.
  prod_count: number
  eval_count: number
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

// 9.2.1 — flat-list view of individual failures, one row per sample
// trace across all active clusters. `source` may be 'production',
// 'eval', or omitted (returns all).
export type FailureSource = 'production' | 'eval'

export interface FailureListItem {
  trace_id: string
  cluster_id: string
  cluster_label: string
  severity: ClusterSeverity
  source: FailureSource
  started_at: string | null
  eval_case_id: string | null
  iteration_index: number | null
}

export interface FailureList {
  workflow_id: string
  items: FailureListItem[]
}

export async function getWorkflowFailureList(
  workflowId: string,
  source?: FailureSource,
  limit?: number,
): Promise<FailureList> {
  const params: string[] = []
  if (source) params.push(`source=${source}`)
  if (limit !== undefined) params.push(`limit=${limit}`)
  const qs = params.length ? `?${params.join('&')}` : ''
  return jsonFetch<FailureList>(
    `/api/workflows/${encodeURIComponent(workflowId)}/failures${qs}`,
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
  langsmith_prompt_id: string | null
  versions: SkillVersionSummary[]
  related_eval_cases: SkillRelatedEvalCase[]
}

export interface DeployRequest {
  decided_by: string
}

export interface DeployResponse {
  proposal_id: string
  state: ProposalState
  // Null for non-skill artifact proposals (description / metric / sim /
  // ui-primitive) — those write directly to the workflow row.
  skill_id: string | null
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

// ---------------------------------------------------------------------------
// LangSmith integration (13.0.2) — credentials + fix delivery
// ---------------------------------------------------------------------------

export interface LangSmithStatus {
  configured: boolean
  last_validated_at: string | null
  validation_status: string | null
}

export interface LangSmithTestResult {
  status: 'ok' | 'invalid' | 'error'
  detail: string | null
}

export interface ShipLangSmithResponse {
  proposal_id: string
  prompt_id: string
  commit_hash: string
  commit_url: string
  already_shipped: boolean
}

export interface LangSmithBindingResponse {
  skill_id: string
  langsmith_prompt_id: string | null
}

export async function getLangSmithStatus(): Promise<LangSmithStatus> {
  return jsonFetch<LangSmithStatus>('/api/integrations/langsmith')
}

export async function setLangSmithCredential(apiKey: string): Promise<LangSmithStatus> {
  return jsonFetch<LangSmithStatus>('/api/integrations/langsmith', {
    method: 'POST',
    body: JSON.stringify({ api_key: apiKey }),
  })
}

export async function deleteLangSmithCredential(): Promise<void> {
  await jsonFetch<unknown>('/api/integrations/langsmith', { method: 'DELETE' })
}

export async function testLangSmithConnection(): Promise<LangSmithTestResult> {
  return jsonFetch<LangSmithTestResult>('/api/integrations/langsmith/test', {
    method: 'POST',
    body: '{}',
  })
}

export async function shipFixToLangSmith(
  proposalId: string,
): Promise<ShipLangSmithResponse> {
  return jsonFetch<ShipLangSmithResponse>(
    `/api/proposals/${encodeURIComponent(proposalId)}/ship-langsmith`,
    { method: 'POST', body: '{}' },
  )
}

export async function setSkillLangSmithBinding(
  skillId: string,
  promptId: string | null,
): Promise<LangSmithBindingResponse> {
  return jsonFetch<LangSmithBindingResponse>(
    `/api/skills/${encodeURIComponent(skillId)}/langsmith-binding`,
    { method: 'PATCH', body: JSON.stringify({ langsmith_prompt_id: promptId }) },
  )
}

// ---------------------------------------------------------------------------
// Copilot Studio integration (13.0.3) — Entra credential + fix delivery
// ---------------------------------------------------------------------------

export interface CopilotStudioStatus {
  configured: boolean
  last_validated_at: string | null
  validation_status: string | null
}

export interface CopilotStudioCredentialInput {
  tenant_id: string
  client_id: string
  client_secret: string
  environment_url: string
  authority_host?: string | null
}

export interface CopilotStudioTestResult {
  status: 'ok' | 'invalid' | 'error'
  detail: string | null
}

export interface ShipCopilotStudioResponse {
  proposal_id: string
  summary: string
  instruction_text: string
  already_delivered: boolean
}

export async function getCopilotStudioStatus(): Promise<CopilotStudioStatus> {
  return jsonFetch<CopilotStudioStatus>('/api/integrations/copilot-studio')
}

export async function setCopilotStudioCredential(
  cred: CopilotStudioCredentialInput,
): Promise<CopilotStudioStatus> {
  return jsonFetch<CopilotStudioStatus>('/api/integrations/copilot-studio', {
    method: 'POST',
    body: JSON.stringify(cred),
  })
}

export async function deleteCopilotStudioCredential(): Promise<void> {
  await jsonFetch<unknown>('/api/integrations/copilot-studio', { method: 'DELETE' })
}

export async function testCopilotStudioConnection(): Promise<CopilotStudioTestResult> {
  return jsonFetch<CopilotStudioTestResult>('/api/integrations/copilot-studio/test', {
    method: 'POST',
    body: '{}',
  })
}

export async function shipFixToCopilotStudio(
  proposalId: string,
): Promise<ShipCopilotStudioResponse> {
  return jsonFetch<ShipCopilotStudioResponse>(
    `/api/proposals/${encodeURIComponent(proposalId)}/ship-copilot-studio`,
    { method: 'POST', body: '{}' },
  )
}

export interface CopilotStudioDefinitionResult {
  agent_definition: string | null
  found: boolean
}

export async function exportCopilotStudioDefinition(
  solutionName: string,
  cookieHeader?: string,
): Promise<CopilotStudioDefinitionResult> {
  return jsonFetch<CopilotStudioDefinitionResult>(
    '/api/integrations/copilot-studio/export-definition',
    {
      method: 'POST',
      body: JSON.stringify({ solution_name: solutionName }),
      headers: cookieHeader ? { cookie: cookieHeader } : undefined,
    },
  )
}

export interface PushEvalCasesCopilotStudioInput {
  agent_id: string
  test_set_name?: string
  cluster_id?: string
  test_fold_only?: boolean
  pushed_by?: string
  /** Safety cap before the MSFT API call. Default 500 on the kernel side.
   *  Use cluster_id to push a targeted subset when a workflow exceeds the cap. */
  max_cases?: number
}

export interface PushEvalCasesCopilotStudioResponse {
  workflow_id: string
  test_set_id: string
  case_count: number
}

export async function pushEvalCasesCopilotStudio(
  workflowId: string,
  body: PushEvalCasesCopilotStudioInput,
): Promise<PushEvalCasesCopilotStudioResponse> {
  return jsonFetch<PushEvalCasesCopilotStudioResponse>(
    `/api/workflows/${encodeURIComponent(workflowId)}/push-eval-cases-copilot-studio`,
    { method: 'POST', body: JSON.stringify(body) },
  )
}

// Agent registry — workspace-wide index of connected agents across origins.

export type AgentOrigin = 'greenfield' | 'langsmith' | 'copilot_studio'
export type AgentStatus = 'active' | 'paused' | 'archived'

export interface Agent {
  id: string
  workflow_id: string
  name: string
  origin: AgentOrigin
  owner: string | null
  status: AgentStatus
  identity_hash: string
  created_at: string
  status_updated_at: string
  last_iteration_at: string | null
  eval_coverage_count: number
  iteration_count: number
}

export interface AgentList {
  items: Agent[]
  total: number
}

export async function listAgents(): Promise<AgentList> {
  return jsonFetch<AgentList>('/api/agents')
}

export async function setAgentStatus(
  agentId: string,
  status: AgentStatus,
): Promise<Agent> {
  return jsonFetch<Agent>(
    `/api/agents/${encodeURIComponent(agentId)}/status`,
    {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    },
  )
}

// ---------------------------------------------------------------------------
// MCP connectors (Track 17.0) — registered servers + OAuth provider flow.
// ---------------------------------------------------------------------------

export type McpAuthKind = 'none' | 'bearer' | 'oauth' | 'service_principal'

export interface McpServer {
  id: string
  name: string
  provider: string
  endpoint_url: string
  transport: 'streamable_http' | 'sse'
  auth_kind: McpAuthKind
  auth_config: Record<string, unknown>
  status: string
  has_secret: boolean
  last_validated_at: string | null
  validation_status: string | null
}

export interface McpServerTestResult {
  status: 'ok' | 'error'
  tool_count: number | null
  detail: string | null
}

export interface McpProviderInfo {
  provider: string
  display_name: string
  default_scopes: string[]
  default_endpoint_url: string
  tenant_scoped: boolean
}

export interface McpOAuthClientView {
  provider: string
  configured: boolean
  client_id: string | null
  config: Record<string, unknown>
}

export async function listMcpServers(): Promise<McpServer[]> {
  return jsonFetch<McpServer[]>('/api/mcp/servers')
}

export async function deleteMcpServer(id: string): Promise<void> {
  await jsonFetch<unknown>(`/api/mcp/servers/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export async function testMcpServer(id: string): Promise<McpServerTestResult> {
  return jsonFetch<McpServerTestResult>(
    `/api/mcp/servers/${encodeURIComponent(id)}/test`,
    { method: 'POST', body: '{}' },
  )
}

export async function listMcpProviders(): Promise<McpProviderInfo[]> {
  return jsonFetch<McpProviderInfo[]>('/api/mcp/oauth/providers')
}

export async function getMcpOAuthClient(provider: string): Promise<McpOAuthClientView> {
  return jsonFetch<McpOAuthClientView>(
    `/api/mcp/oauth/${encodeURIComponent(provider)}/client`,
  )
}

export async function setMcpOAuthClient(
  provider: string,
  body: { client_id: string; client_secret: string; config?: Record<string, unknown> },
): Promise<McpOAuthClientView> {
  return jsonFetch<McpOAuthClientView>(
    `/api/mcp/oauth/${encodeURIComponent(provider)}/client`,
    { method: 'PUT', body: JSON.stringify({ config: {}, ...body }) },
  )
}

export async function deleteMcpOAuthClient(provider: string): Promise<void> {
  await jsonFetch<unknown>(
    `/api/mcp/oauth/${encodeURIComponent(provider)}/client`,
    { method: 'DELETE' },
  )
}

export async function startMcpOAuth(
  provider: string,
  body: { server_name: string; scopes?: string[]; endpoint_url?: string },
): Promise<{ authorize_url: string }> {
  return jsonFetch<{ authorize_url: string }>(
    `/api/mcp/oauth/${encodeURIComponent(provider)}/start`,
    { method: 'POST', body: JSON.stringify(body) },
  )
}
