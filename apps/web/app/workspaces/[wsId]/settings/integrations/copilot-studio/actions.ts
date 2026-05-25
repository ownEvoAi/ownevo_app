'use server'

import { revalidatePath } from 'next/cache'
import {
  type CopilotStudioCredentialInput,
  deleteCopilotStudioCredential,
  KernelApiError,
  setCopilotStudioCredential,
  testCopilotStudioConnection,
} from '@/lib/api'

type ActionResult =
  | { ok: true }
  | { ok: false; error: string }

type TestResult =
  | { ok: true; status: string; detail: string | null }
  | { ok: false; error: string }

function errorMessage(err: unknown): string {
  if (err instanceof KernelApiError) return err.detail
  return err instanceof Error ? err.message : 'Unknown error'
}

export async function saveCopilotStudioCredentialAction(
  wsId: string,
  cred: CopilotStudioCredentialInput,
): Promise<ActionResult> {
  if (
    !cred.tenant_id.trim() ||
    !cred.client_id.trim() ||
    !cred.client_secret.trim() ||
    !cred.environment_url.trim()
  ) {
    return { ok: false, error: 'Tenant ID, client ID, client secret, and environment URL are all required.' }
  }
  try {
    await setCopilotStudioCredential({
      tenant_id: cred.tenant_id.trim(),
      client_id: cred.client_id.trim(),
      client_secret: cred.client_secret.trim(),
      environment_url: cred.environment_url.trim(),
      authority_host: cred.authority_host?.trim() || null,
    })
  } catch (err) {
    return { ok: false, error: errorMessage(err) }
  }
  revalidatePath(`/workspaces/${wsId}/settings/integrations/copilot-studio`)
  return { ok: true }
}

export async function testCopilotStudioConnectionAction(wsId: string): Promise<TestResult> {
  try {
    const res = await testCopilotStudioConnection()
    revalidatePath(`/workspaces/${wsId}/settings/integrations/copilot-studio`)
    return { ok: true, status: res.status, detail: res.detail }
  } catch (err) {
    return { ok: false, error: errorMessage(err) }
  }
}

export async function deleteCopilotStudioCredentialAction(wsId: string): Promise<ActionResult> {
  try {
    await deleteCopilotStudioCredential()
  } catch (err) {
    return { ok: false, error: errorMessage(err) }
  }
  revalidatePath(`/workspaces/${wsId}/settings/integrations/copilot-studio`)
  return { ok: true }
}
