'use server'

import { revalidatePath } from 'next/cache'
import {
 kernelErrorMessage,
 verifyAuditChain,
 type AuditVerifyResponse,
} from '../../../../lib/api'

export interface VerifyState {
 ok: boolean
 result: AuditVerifyResponse | null
 error: string | null
}

// Server Action — fires the chain-integrity check from the verify
// button. Returns a small state object the page renders inline.
//
// `revalidatePath` re-fetches the audit list after the verify call so
// the row count + most-recent entry stay in sync (e.g. when the verify
// itself produces an audit entry in a future iteration).
export async function verifyAuditChainAction(
 wsId: string,
): Promise<VerifyState> {
 try {
 const result = await verifyAuditChain()
 revalidatePath(`/workspaces/${wsId}/audit`)
 return { ok: true, result, error: null }
 } catch (err) {
 return { ok: false, result: null, error: kernelErrorMessage(err) }
 }
}
