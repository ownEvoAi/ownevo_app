'use server'

import { KernelApiError, tryWorkflow, type TryItRequest, type TryItResponse } from '@/lib/api'

export interface TryWorkflowResult {
 data: TryItResponse | null
 error: string | null
}

export async function tryWorkflowAction(
 wfId: string,
 body: TryItRequest,
): Promise<TryWorkflowResult> {
 try {
 return { data: await tryWorkflow(wfId, body), error: null }
 } catch (err) {
 const msg =
 err instanceof KernelApiError
 ? err.message
 : err instanceof Error
 ? err.message
 : String(err)
 return { data: null, error: msg }
 }
}
