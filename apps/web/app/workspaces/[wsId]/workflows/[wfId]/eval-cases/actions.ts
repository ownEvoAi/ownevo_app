'use server'

import { revalidatePath } from 'next/cache'
import {
 generateEvalCases,
 KernelApiError,
 pushEvalCasesCopilotStudio,
} from '@/lib/api'

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

export interface PushEvalCasesActionState {
 error: string | null
 result: { testSetId: string; caseCount: number } | null
}

export async function pushEvalCasesCopilotStudioAction(
 wsId: string,
 wfId: string,
 _prev: PushEvalCasesActionState,
 formData: FormData,
): Promise<PushEvalCasesActionState> {
 const agentId = String(formData.get('agent_id') ?? '').trim if (!agentId) {
 return { error: 'Copilot Studio agent id is required.', result: null }
 }
 const testSetName = String(formData.get('test_set_name') ?? '').trim const testFoldOnly = formData.get('test_fold_only') === 'on'
 try {
 const res = await pushEvalCasesCopilotStudio(wfId, {
 agent_id: agentId,
 test_set_name: testSetName || undefined,
 test_fold_only: testFoldOnly,
 })
 revalidatePath(`/workspaces/${wsId}/workflows/${wfId}/eval-cases`)
 revalidatePath(`/workspaces/${wsId}/activity`)
 revalidatePath(`/workspaces/${wsId}/workflows/${wfId}/audit`)
 return {
 error: null,
 result: { testSetId: res.test_set_id, caseCount: res.case_count },
 }
 } catch (err) {
 if (err instanceof KernelApiError) {
 return {
 error: `Kernel error (${err.status}): ${err.detail}`,
 result: null,
 }
 }
 return {
 error: err instanceof Error ? err.message : String(err),
 result: null,
 }
 }
}
