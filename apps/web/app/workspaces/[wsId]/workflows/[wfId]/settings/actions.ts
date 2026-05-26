'use server'

import { redirect } from 'next/navigation'
import { revalidatePath } from 'next/cache'
import {
 deleteWorkflow as deleteWorkflowApi,
 KernelApiError,
 setSkillLangSmithBinding,
 updateWorkflowAgentModel,
 updateWorkflowDescription,
 type WorkflowDeleteResponse,
} from '@/lib/api'

interface UpdateDescriptionInput {
 wsId: string
 wfId: string
 description: string
}

type UpdateResult =
 | { ok: true }
 | { ok: false; error: string }

export async function updateDescriptionAction(
 input: UpdateDescriptionInput,
): Promise<UpdateResult> {
 const trimmed = input.description.trim if (trimmed.length < 10) {
 return {
 ok: false,
 error: 'Description must be at least 10 characters.',
 }
 }
 try {
 await updateWorkflowDescription(input.wfId, trimmed)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }

 // Every workspace page reads the description (sidebar, Health rows,
 // page header). Invalidate the workflow shell + Health.
 revalidatePath(`/workspaces/${input.wsId}/workflows/${input.wfId}`, 'layout')
 revalidatePath(`/workspaces/${input.wsId}`)
 return { ok: true }
}

interface UpdateAgentModelInput {
 wsId: string
 wfId: string
 agentModelId: string
}

export async function updateAgentModelAction(
 input: UpdateAgentModelInput,
): Promise<UpdateResult> {
 const slug = input.agentModelId.trim if (!slug.includes(':')) {
 return {
 ok: false,
 error: 'Model id must be a provider:model slug.',
 }
 }
 try {
 await updateWorkflowAgentModel(input.wfId, slug)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }

 // Settings page reads agent_model_id; Audit tab will pick up the
 // new audit_entries row on its own server fetch.
 revalidatePath(`/workspaces/${input.wsId}/workflows/${input.wfId}`, 'layout')
 return { ok: true }
}


interface DeleteWorkflowInput {
 wsId: string
 wfId: string
 confirmation: string
}

type DeleteResult =
 | { ok: true; counts: WorkflowDeleteResponse }
 | { ok: false; error: string }

// The deletion is hard + cascading. We require the operator to retype
// the workflow id as a confirmation token so a stray click on the
// danger button can't drop a workflow they didn't mean to. The same
// guard the kernel-level confirm-and-cascade enforces (D4 single
// tenant: no soft-delete column to fall back on).
export async function deleteWorkflowAction(
 input: DeleteWorkflowInput,
): Promise<DeleteResult> {
 if (input.confirmation.trim !== input.wfId) {
 return {
 ok: false,
 error: `Type "${input.wfId}" to confirm deletion.`,
 }
 }

 let counts: WorkflowDeleteResponse
 try {
 counts = await deleteWorkflowApi(input.wfId)
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
 redirect(`/workspaces/${input.wsId}?deleted=${encodeURIComponent(counts.id)}`)
}

interface BindingInput {
 wsId: string
 wfId: string
 skillId: string
 promptId: string | null
}

export async function updateLangSmithBindingAction(
 input: BindingInput,
): Promise<UpdateResult> {
 try {
 await setSkillLangSmithBinding(input.skillId, input.promptId)
 } catch (err) {
 if (err instanceof KernelApiError) {
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }
 revalidatePath(`/workspaces/${input.wsId}/workflows/${input.wfId}/settings`)
 return { ok: true }
}
