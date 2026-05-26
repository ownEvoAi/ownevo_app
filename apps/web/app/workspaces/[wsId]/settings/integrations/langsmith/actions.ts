'use server'

import { revalidatePath } from 'next/cache'
import {
 deleteLangSmithCredential,
 KernelApiError,
 setLangSmithCredential,
 testLangSmithConnection,
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

export async function saveLangSmithKeyAction(
 wsId: string,
 apiKey: string,
): Promise<ActionResult> {
 if (!apiKey.trim ) {
 return { ok: false, error: 'API key must not be empty.' }
 }
 try {
 await setLangSmithCredential(apiKey.trim )
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
 revalidatePath(`/workspaces/${wsId}/settings/integrations/langsmith`)
 return { ok: true }
}

export async function testLangSmithKeyAction(wsId: string): Promise<TestResult> {
 try {
 const res = await testLangSmithConnection revalidatePath(`/workspaces/${wsId}/settings/integrations/langsmith`)
 return { ok: true, status: res.status, detail: res.detail }
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
}

export async function deleteLangSmithKeyAction(wsId: string): Promise<ActionResult> {
 try {
 await deleteLangSmithCredential } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
 revalidatePath(`/workspaces/${wsId}/settings/integrations/langsmith`)
 return { ok: true }
}
