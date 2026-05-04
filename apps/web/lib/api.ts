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
      const body = (await res.json()) as { detail?: string }
      if (body?.detail) detail = body.detail
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
