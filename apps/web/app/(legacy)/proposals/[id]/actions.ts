'use server'

import { revalidatePath } from 'next/cache'
import { approveProposal, rejectProposal, KernelApiError } from '@/lib/api'

interface DecideInput {
  proposalId: string
  decision: 'approve' | 'reject'
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

  const fn = input.decision === 'approve' ? approveProposal : rejectProposal
  try {
    const res = await fn(input.proposalId, {
      decided_by: input.decidedBy,
      comment: input.comment,
    })
    // Invalidate both routes so the next render picks up the new state.
    revalidatePath('/inbox')
    revalidatePath(`/proposals/${input.proposalId}`)
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
