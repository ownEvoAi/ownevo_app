import { cache } from 'react'
import { cookies } from 'next/headers'
import { isDemoMode } from './demo-mode'

// Only these two cookies are relevant to the kernel's demo identity resolver.
// Forward only them so unrelated cookies (analytics, framework internals) are
// not leaked to the kernel. Must match `DEMO_COOKIE_NAMES` in api-server.ts.
const DEMO_COOKIE_NAMES: readonly string[] = ['ownevo_demo_id', 'ownevo_demo_invite']

// Shape mirrors `DemoStatusResponse` in apps/kernel/.../routes/demo.py.
export interface DemoStatus {
 demoMode: boolean
 tier: 'anonymous' | 'elevated' | 'unlimited' | null
 label: string | null
 usedTokens: number
 limitTokens: number | null
 exhausted: boolean
 budgetExhausted: boolean
 resetAt: string | null
 inviteExp: number | null
}

const FALLBACK: DemoStatus = {
 demoMode: false,
 tier: null,
 label: null,
 usedTokens: 0,
 limitTokens: null,
 exhausted: false,
 budgetExhausted: false,
 resetAt: null,
 inviteExp: null,
}

// Server-only: pulls `/api/demo/status` from the kernel, forwarding
// the incoming visitor's cookies so the kernel can resolve their tier
// and per-day usage. Returns a safe fallback (demoMode=false) on
// network errors and when DEMO_MODE is off — pages then render as
// usual.
//
// Wrapped with React `cache ` so multiple callers within the same
// server render (e.g. the root-layout DemoBanner + a page-level gate
// check) share one network round-trip.
export const getDemoStatus = cache(async : Promise<DemoStatus> => {
 if (!isDemoMode ) return FALLBACK
 const apiUrl = process.env.OWNEVO_KERNEL_API_URL || 'http://localhost:8000'
 const jar = await cookies const cookieHeader = jar
 .getAll .filter((c) => DEMO_COOKIE_NAMES.includes(c.name))
 .map((c) => `${c.name}=${c.value}`)
 .join('; ')

 try {
 const res = await fetch(`${apiUrl}/api/demo/status`, {
 cache: 'no-store',
 headers: cookieHeader ? { cookie: cookieHeader } : undefined,
 })
 if (!res.ok) return FALLBACK
 const body = (await res.json ) as Record<string, unknown>
 return {
 demoMode: Boolean(body.demo_mode),
 tier: (body.tier as DemoStatus['tier']) ?? null,
 label: (body.label as string | null) ?? null,
 usedTokens: Number(body.used_tokens ?? 0),
 limitTokens: body.limit_tokens === null || body.limit_tokens === undefined
 ? null
 : Number(body.limit_tokens),
 exhausted: Boolean(body.exhausted),
 budgetExhausted: Boolean(body.budget_exhausted),
 resetAt: (body.reset_at as string | null) ?? null,
 inviteExp: body.invite_exp === null || body.invite_exp === undefined
 ? null
 : Number(body.invite_exp),
 }
 } catch {
 return FALLBACK
 }
})

// Disabled-state computed once on the server so the CTA renders
// inert without any client hydration. `reason` is the tooltip copy.
export interface DemoGateState {
 disabled: boolean
 reason: string | null
}

export function gateStateFor(status: DemoStatus): DemoGateState {
 if (!status.demoMode) return { disabled: false, reason: null }
 if (status.budgetExhausted) {
 return {
 disabled: true,
 reason:
 "The demo's daily LLM budget is exhausted — back tomorrow.",
 }
 }
 if (status.exhausted) {
 const resetCopy = status.resetAt
 ? ` Resets at ${new Date(status.resetAt).toUTCString }.`
 : ''
 return {
 disabled: true,
 reason: `You've used today's demo quota.${resetCopy} Have an invite? Paste the token to continue.`,
 }
 }
 return { disabled: false, reason: null }
}
