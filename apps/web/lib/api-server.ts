// Server-only wrappers for API calls that need to forward demo cookies.
//
// This module imports `next/headers` and must only be imported from
// Server Components or Server Actions ('use server'). Client components
// use `@/lib/api` directly — which never imports next/headers.
import 'server-only'
import { cookies } from 'next/headers'
import { isDemoMode } from './demo-mode'
import {
  generateWorkflow as _generateWorkflow,
  fetchNextDiscoveryQuestion as _fetchNextDiscoveryQuestion,
  fetchImportNextQuestion as _fetchImportNextQuestion,
  generateFromImport as _generateFromImport,
  type DesignAgentLog,
  type GenerateWorkflowResponse,
  type ImportGenerateResponse,
  type NextDiscoveryQuestionResponse,
  type PriorDiscoveryAnswer,
} from './api'

// Re-export everything else from the base module so callers can use a
// single import path and get cookie forwarding for free on the two
// endpoints that need it.
export * from './api'

const DEMO_COOKIE_NAMES = ['ownevo_demo_id', 'ownevo_demo_invite'] as const

async function getDemoCookieHeader(): Promise<string | undefined> {
  if (!isDemoMode()) return undefined
  const jar = await cookies()
  const relevant = jar
    .getAll()
    .filter((c) => (DEMO_COOKIE_NAMES as readonly string[]).includes(c.name))
    .map((c) => `${c.name}=${c.value}`)
    .join('; ')
  return relevant || undefined
}

export async function generateWorkflow(
  description: string,
  workflowId?: string,
  templateId?: string,
  designAgentLog?: DesignAgentLog | null,
): Promise<GenerateWorkflowResponse> {
  const cookieHeader = await getDemoCookieHeader()
  return _generateWorkflow(description, workflowId, templateId, designAgentLog, cookieHeader)
}

export async function fetchNextDiscoveryQuestion(
  description: string,
  templateId: string | null,
  priorAnswers: PriorDiscoveryAnswer[],
  signal?: AbortSignal,
): Promise<NextDiscoveryQuestionResponse> {
  const cookieHeader = await getDemoCookieHeader()
  return _fetchNextDiscoveryQuestion(
    description,
    templateId,
    priorAnswers,
    signal,
    cookieHeader,
  )
}

export async function fetchImportNextQuestion(
  traceIds: string[],
  agentDefinition: string | null,
  priorAnswers: PriorDiscoveryAnswer[],
  signal?: AbortSignal,
): Promise<NextDiscoveryQuestionResponse> {
  const cookieHeader = await getDemoCookieHeader()
  return _fetchImportNextQuestion(
    traceIds,
    agentDefinition,
    priorAnswers,
    signal,
    cookieHeader,
  )
}

export async function generateFromImport(
  traceIds: string[],
  agentDefinition: string | null,
  designAgentLog: DesignAgentLog | null,
  workflowId?: string,
): Promise<ImportGenerateResponse> {
  const cookieHeader = await getDemoCookieHeader()
  return _generateFromImport(
    traceIds,
    agentDefinition,
    designAgentLog,
    workflowId,
    cookieHeader,
  )
}
