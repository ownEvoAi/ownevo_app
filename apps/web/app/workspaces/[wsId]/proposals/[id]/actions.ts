'use server'

import { revalidatePath } from 'next/cache'
import {
  approveProposal,
  deployProposal,
  KernelApiError,
  rejectProposal,
  requestChangesProposal,
  rollbackProposal,
  shipFixToLangSmith,
} from '@/lib/api'

interface DecideInput {
  proposalId: string
  wsId: string
  decision: 'approve' | 'reject' | 'request-changes'
  decidedBy: string
  comment?: string
}

type DecideResult =
  | { ok: true; state: string }
  | { ok: false; error: string }

// Server Action — runs in the Next.js server, holds the kernel API URL,
// returns a structured result for the client island to render. We use
// a return-value protocol (instead of throwing) so the form can show
// inline errors without crashing the page.
export async function decideAction(input: DecideInput): Promise<DecideResult> {
  if (!input.decidedBy.trim()) {
    return { ok: false, error: 'Reviewer identity is required.' }
  }

  const fn =
    input.decision === 'approve'
      ? approveProposal
      : input.decision === 'request-changes'
        ? requestChangesProposal
        : rejectProposal
  try {
    const res = await fn(input.proposalId, {
      decided_by: input.decidedBy,
      comment: input.comment,
    })
    // Invalidate every workspace surface that renders this proposal's
    // state: the proposal page itself, the workspace inbox (where
    // gate-passed proposals queue), the workflow Failures view (cluster
    // → proposal click-through), the workspace audit log, and Health.
    // Legacy /inbox + /proposals/<id> are 307 redirects now, so they
    // don't need revalidation.
    revalidatePath(`/workspaces/${input.wsId}/proposals/${input.proposalId}`)
    revalidatePath(`/workspaces/${input.wsId}/inbox`)
    revalidatePath(`/workspaces/${input.wsId}/audit`)
    revalidatePath(`/workspaces/${input.wsId}`)
    return { ok: true, state: res.state }
  } catch (err) {
    if (err instanceof KernelApiError) {
      // 409 (illegal state) is the most common user-facing error here.
      // Surface the kernel's detail string verbatim — it already names
      // the actual state vs the expected gate-passed.
      return { ok: false, error: err.detail }
    }
    return {
      ok: false,
      error: err instanceof Error ? err.message : 'Unknown error',
    }
  }
}

interface DeployInput {
  proposalId: string
  wsId: string
  workflowId: string
  action: 'deploy' | 'rollback'
  decidedBy: string
}

type DeployResult =
  | { ok: true; state: string }
  | { ok: false; error: string }

// Deploy / rollback share the same shape: POST to the kernel with the
// reviewer identity, then revalidate the surfaces that show the
// proposal state (proposal page, workflow Overview / Proposals,
// skill detail, Health). We use a unified action with an `action`
// discriminator so the client island has one code path.
export async function deployAction(input: DeployInput): Promise<DeployResult> {
  if (!input.decidedBy.trim()) {
    return { ok: false, error: 'Reviewer identity is required.' }
  }
  const fn = input.action === 'deploy' ? deployProposal : rollbackProposal
  try {
    const res = await fn(input.proposalId, { decided_by: input.decidedBy })
    revalidatePath(`/workspaces/${input.wsId}/proposals/${input.proposalId}`)
    revalidatePath(`/workspaces/${input.wsId}/workflows/${input.workflowId}`, 'layout')
    revalidatePath(`/workspaces/${input.wsId}/skills`)
    revalidatePath(`/workspaces/${input.wsId}/audit`)
    revalidatePath(`/workspaces/${input.wsId}`)
    return { ok: true, state: res.state }
  } catch (err) {
    if (err instanceof KernelApiError) {
      return { ok: false, error: err.detail }
    }
    return {
      ok: false,
      error: err instanceof Error ? err.message : 'Unknown error',
    }
  }
}

interface ShipLangSmithInput {
  proposalId: string
  wsId: string
}

type ShipResult =
  | { ok: true; commitUrl: string; commitHash: string; alreadyShipped: boolean }
  | { ok: false; error: string }

// Pushes a deployed fix back to LangSmith. Backend enforces the
// preconditions (origin, binding, deployed state, credential) and
// returns a clear 4xx the form surfaces inline — so the button can be
// shown for any deployed skill proposal without the page pre-fetching
// all the gating data.
export async function shipLangSmithAction(
  input: ShipLangSmithInput,
): Promise<ShipResult> {
  try {
    const res = await shipFixToLangSmith(input.proposalId)
    revalidatePath(`/workspaces/${input.wsId}/proposals/${input.proposalId}`)
    return {
      ok: true,
      commitUrl: res.commit_url,
      commitHash: res.commit_hash,
      alreadyShipped: res.already_shipped,
    }
  } catch (err) {
    if (err instanceof KernelApiError) {
      return { ok: false, error: err.detail }
    }
    return { ok: false, error: err instanceof Error ? err.message : 'Unknown error' }
  }
}
