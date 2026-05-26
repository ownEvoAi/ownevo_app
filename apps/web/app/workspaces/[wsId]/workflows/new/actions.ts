'use server'

import { redirect } from 'next/navigation'
import { generateWorkflow, KernelApiError } from '@/lib/api-server'

export interface GenerateState {
 error: string | null
}

export async function generateWorkflowAction(
 wsId: string,
 _prev: GenerateState,
 formData: FormData,
): Promise<GenerateState> {
 const description = String(formData.get('description') ?? '').trim const workflowIdInput = String(formData.get('workflow_id') ?? '').trim const workflowId = workflowIdInput || undefined
 const templateIdInput = String(formData.get('template_id') ?? '').trim const templateId = templateIdInput || undefined

 if (description.length < 50) {
 return {
 error: 'Description must be at least 50 characters.',
 }
 }

 let result
 try {
 result = await generateWorkflow(description, workflowId, templateId)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { error: `Kernel error (${err.status}): ${err.detail}` }
 }
 return { error: err instanceof Error ? err.message : String(err) }
 }

 // Land on the review step instead of the workflow detail. The spec
 // + sim plan are committed to DB, but eval cases haven't been
 // generated yet — let the operator confirm what was produced (and
 // optionally revise) before the loop starts spending tokens.
 redirect(
 `/workspaces/${wsId}/workflows/new/review/${encodeURIComponent(result.workflow_id)}`,
 )
}
