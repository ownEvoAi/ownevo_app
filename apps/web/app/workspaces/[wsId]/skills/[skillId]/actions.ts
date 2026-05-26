'use server'

import { revalidatePath } from 'next/cache'
import {
 deployProposal,
 rollbackProposal,
 KernelApiError,
} from '@/lib/api'

interface DeployInput {
 proposalId: string
 wsId: string
 skillId: string
 action: 'deploy' | 'rollback'
 decidedBy: string
}

type DeployResult =
 | { ok: true; state: string }
 | { ok: false; error: string }

// Server Action — runs in the Next.js server, holds the kernel API URL,
// returns a structured result for the client island to render. We use
// a return-value protocol (instead of throwing) so the form can show
// inline errors without crashing the page.
export async function deployAction(input: DeployInput): Promise<DeployResult> {
 if (!input.decidedBy.trim() ) {
 return { ok: false, error: 'Reviewer identity is required.' }
 }

 const fn = input.action === 'deploy' ? deployProposal : rollbackProposal
 try {
 const res = await fn(input.proposalId, { decided_by: input.decidedBy })
 // Invalidate every workspace surface that renders this skill's
 // production pointer or this proposal's state: skill detail (the
 // page that hosts the buttons), the workspace inbox (lists
 // approved-awaiting-deploy), the audit log (the new
 // proposal-deployed/rolled-back entry), and Health (lift chart
 // semantics unchanged but proposal counts may shift).
 revalidatePath(`/workspaces/${input.wsId}/skills/${input.skillId}`)
 revalidatePath(`/workspaces/${input.wsId}/proposals/${input.proposalId}`)
 revalidatePath(`/workspaces/${input.wsId}/inbox`)
 revalidatePath(`/workspaces/${input.wsId}/audit`)
 revalidatePath(`/workspaces/${input.wsId}`)
 return { ok: true, state: res.state }
 } catch (err) {
 if (err instanceof KernelApiError) {
 // 409 (illegal state) is the most common user-facing error here.
 // Surface the kernel's detail string verbatim — it already names
 // the actual state vs the expected state.
 return { ok: false, error: err.detail }
 }
 return {
 ok: false,
 error: err instanceof Error ? err.message : 'Unknown error',
 }
 }
}
