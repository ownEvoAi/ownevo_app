'use server'

import { revalidatePath } from 'next/cache'
import { generateEvalCases, KernelApiError } from '@/lib/api'

export interface EvalCasesActionState {
  error: string | null
  generated: number | null
}

export async function generateEvalCasesAction(
  wsId: string,
  wfId: string,
  _prev: EvalCasesActionState,
): Promise<EvalCasesActionState> {
  try {
    const result = await generateEvalCases(wfId)
    revalidatePath(`/workspaces/${wsId}/workflows/${wfId}/eval-cases`)
    return { error: null, generated: result.generated }
  } catch (err) {
    if (err instanceof KernelApiError) {
      return {
        error: `Kernel error (${err.status}): ${err.detail}`,
        generated: null,
      }
    }
    return {
      error: err instanceof Error ? err.message : String(err),
      generated: null,
    }
  }
}
