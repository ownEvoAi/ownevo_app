'use server'

import { redirect } from 'next/navigation'
import { revalidatePath } from 'next/cache'
import { deleteWorkflow as deleteWorkflowApi, KernelApiError } from '@/lib/api'

interface ReviseInput {
 wsId: string
 wfId: string
}

type ReviseResult = { ok: true } | { ok: false; error: string }

// Revise = delete + back to the describe step. Kernel cascades skills +
// traces + audit (same DELETE path as the Settings tab), so this is
// safe to run unconfirmed: the only thing the user can lose is the
// just-generated spec, which is exactly what they want to throw away.
export async function reviseWorkflowAction(
 input: ReviseInput,
): Promise<ReviseResult> {
 try {
 await deleteWorkflowApi(input.wfId)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }
 revalidatePath(`/workspaces/${input.wsId}`, 'layout')
 redirect(`/workspaces/${input.wsId}/workflows/new`)
}
