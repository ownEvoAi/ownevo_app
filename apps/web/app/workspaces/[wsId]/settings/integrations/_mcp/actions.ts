'use server'

import { revalidatePath } from 'next/cache'
import {
 deleteMcpOAuthClient,
 deleteMcpServer,
 KernelApiError,
 setMcpOAuthClient,
 startMcpOAuth,
 testMcpServer,
} from '@/lib/api'

// provider id -> settings route slug (must match the kernel's _UI_SLUG map).
const UI_SLUG: Record<string, string> = {
 slack: 'slack',
 google_workspace: 'google-workspace',
 microsoft_365: 'microsoft-365',
}

type ActionResult = { ok: true } | { ok: false; error: string }
type StartResult = { ok: true; authorizeUrl: string } | { ok: false; error: string }
type TestResult =
 | { ok: true; status: string; toolCount: number | null; detail: string | null }
 | { ok: false; error: string }

function errorMessage(err: unknown): string {
 if (err instanceof KernelApiError) return err.detail
 return err instanceof Error ? err.message : 'Unknown error'
}

function settingsPath(wsId: string, provider: string): string {
 return `/workspaces/${wsId}/settings/integrations/${UI_SLUG[provider] ?? provider}`
}

export async function saveMcpClientAction(
 wsId: string,
 provider: string,
 clientId: string,
 clientSecret: string,
 tenant: string | null,
): Promise<ActionResult> {
 if (!clientId.trim || !clientSecret.trim ) {
 return { ok: false, error: 'Client ID and client secret are both required.' }
 }
 try {
 await setMcpOAuthClient(provider, {
 client_id: clientId.trim ,
 client_secret: clientSecret.trim ,
 config: tenant && tenant.trim ? { tenant: tenant.trim } : {},
 })
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
 revalidatePath(settingsPath(wsId, provider))
 return { ok: true }
}

export async function deleteMcpClientAction(
 wsId: string,
 provider: string,
): Promise<ActionResult> {
 try {
 await deleteMcpOAuthClient(provider)
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
 revalidatePath(settingsPath(wsId, provider))
 return { ok: true }
}

export async function startMcpConnectAction(
 provider: string,
 serverName: string,
): Promise<StartResult> {
 if (!serverName.trim ) {
 return { ok: false, error: 'A connection name is required.' }
 }
 try {
 const res = await startMcpOAuth(provider, { server_name: serverName.trim })
 return { ok: true, authorizeUrl: res.authorize_url }
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
}

export async function testMcpServerAction(
 wsId: string,
 provider: string,
 serverId: string,
): Promise<TestResult> {
 try {
 const res = await testMcpServer(serverId)
 revalidatePath(settingsPath(wsId, provider))
 return { ok: true, status: res.status, toolCount: res.tool_count, detail: res.detail }
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
}

export async function removeMcpServerAction(
 wsId: string,
 provider: string,
 serverId: string,
): Promise<ActionResult> {
 try {
 await deleteMcpServer(serverId)
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
 revalidatePath(settingsPath(wsId, provider))
 return { ok: true }
}
