'use server'

import { revalidatePath } from 'next/cache'
import {
 KernelApiError,
 setAgentStatus,
 type AgentStatus,
} from '@/lib/api'

interface UpdateStatusInput {
 wsId: string
 agentId: string
 status: AgentStatus
}

type UpdateResult = { ok: true } | { ok: false; error: string }

export async function updateAgentStatusAction(
 input: UpdateStatusInput,
): Promise<UpdateResult> {
 try {
 await setAgentStatus(input.agentId, input.status)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }
 revalidatePath(`/workspaces/${input.wsId}/agents`)
 return { ok: true }
}
