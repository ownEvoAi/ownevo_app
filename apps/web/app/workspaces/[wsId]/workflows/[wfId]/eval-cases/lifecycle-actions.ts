'use server'

import { revalidatePath } from 'next/cache'
import {
 createEvalCase,
 deleteEvalCase,
 KernelApiError,
 type EvalCaseCreatePayload,
} from '@/lib/api'

interface AddInput {
 wsId: string
 wfId: string
 payload: EvalCaseCreatePayload
}

type AddResult = { ok: true } | { ok: false; error: string }

export async function addEvalCaseAction(input: AddInput): Promise<AddResult> {
 if (!input.payload.case_id.trim() ) {
 return { ok: false, error: 'case_id is required.' }
 }
 if (!input.payload.target_label_field.trim() ) {
 return { ok: false, error: 'target_label_field is required.' }
 }
 try {
 await createEvalCase(input.wfId, input.payload)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }
 revalidatePath(`/workspaces/${input.wsId}/workflows/${input.wfId}/eval-cases`)
 revalidatePath(`/workspaces/${input.wsId}/workflows/${input.wfId}`)
 return { ok: true }
}

interface DeleteInput {
 wsId: string
 wfId: string
 caseId: string
}

export async function deleteEvalCaseAction(
 input: DeleteInput,
): Promise<AddResult> {
 try {
 await deleteEvalCase(input.wfId, input.caseId)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }
 revalidatePath(`/workspaces/${input.wsId}/workflows/${input.wfId}/eval-cases`)
 revalidatePath(`/workspaces/${input.wsId}/workflows/${input.wfId}`)
 return { ok: true }
}
