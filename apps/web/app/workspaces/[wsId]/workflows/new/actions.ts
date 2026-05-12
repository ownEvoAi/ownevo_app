'use server'

import { redirect } from 'next/navigation'
import { generateWorkflow, KernelApiError } from '@/lib/api'

export interface GenerateState {
  error: string | null
}

export async function generateWorkflowAction(
  wsId: string,
  _prev: GenerateState,
  formData: FormData,
): Promise<GenerateState> {
  const description = String(formData.get('description') ?? '').trim()
  const workflowIdInput = String(formData.get('workflow_id') ?? '').trim()
  const workflowId = workflowIdInput || undefined

  if (description.length < 50) {
    return {
      error: 'Description must be at least 50 characters.',
    }
  }

  let result
  try {
    result = await generateWorkflow(description, workflowId)
  } catch (err) {
    if (err instanceof KernelApiError) {
      return { error: `Kernel error (${err.status}): ${err.detail}` }
    }
    return { error: err instanceof Error ? err.message : String(err) }
  }

  redirect(`/workspaces/${wsId}/workflows/${encodeURIComponent(result.workflow_id)}`)
}
