'use server'

import { revalidatePath } from 'next/cache'
import { redirect } from 'next/navigation'
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
 let r
 try {
 r = await runWorkflowIteration(wfId)
 revalidatePath(`/workspaces/${wsId}/workflows/${wfId}`)
 revalidatePath(`/workspaces/${wsId}/inbox`)
 revalidatePath(`/workspaces/${wsId}`)
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

 // First-iteration redirect to the baseline-complete landing — mock
 // 19 parity. After iteration #0 the operator gets a "here's what
 // changed" page instead of being dropped back on Overview, where
 // the next-step card would just say "Improvement loop active". For
 // every later iteration we keep the inline result card.
 if (r.iteration_index === 0) {
 redirect(`/workspaces/${wsId}/workflows/baseline/${wfId}`)
 }

 return {
 error: null,
 iterationIndex: r.iteration_index,
 valScore: r.val_score,
 nFailed: r.n_failed,
 nCases: r.n_cases,
 proposalId: r.proposal_id,
 }
}
