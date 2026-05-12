'use server'

import { revalidatePath } from 'next/cache'
import { KernelApiError, runWorkflowIteration } from '@/lib/api'

export interface RunIterationState {
  error: string | null
  iterationIndex: number | null
  valScore: number | null
  nFailed: number | null
  nCases: number | null
  proposalId: string | null
}

export async function runIterationAction(
  wsId: string,
  wfId: string,
  _prev: RunIterationState,
): Promise<RunIterationState> {
  try {
    const r = await runWorkflowIteration(wfId)
    revalidatePath(`/workspaces/${wsId}/workflows/${wfId}`)
    revalidatePath(`/workspaces/${wsId}/inbox`)
    revalidatePath(`/workspaces/${wsId}`)
    return {
      error: null,
      iterationIndex: r.iteration_index,
      valScore: r.val_score,
      nFailed: r.n_failed,
      nCases: r.n_cases,
      proposalId: r.proposal_id,
    }
  } catch (err) {
    if (err instanceof KernelApiError) {
      return {
        error: `Kernel error (${err.status}): ${err.detail}`,
        iterationIndex: null,
        valScore: null,
        nFailed: null,
        nCases: null,
        proposalId: null,
      }
    }
    return {
      error: err instanceof Error ? err.message : String(err),
      iterationIndex: null,
      valScore: null,
      nFailed: null,
      nCases: null,
      proposalId: null,
    }
  }
}
