'use server'

import { redirect } from 'next/navigation'
import {
  fetchImportNextQuestion,
  fetchImportSummary,
  generateFromImport,
  KernelApiError,
  type DesignAgentLog,
  type DesignAgentLogEntry,
  type DiscoveryQuestionKind,
  type NextDiscoveryQuestion,
  type NextDiscoveryQuestionResponse,
  type PriorDiscoveryAnswer,
  type ReverseDiscoveryInput,
  type WorkflowOrigin,
} from '@/lib/api-server'

const _DISCOVERY_KINDS: ReadonlySet<DiscoveryQuestionKind> = new Set([
  'metric',
  'ambiguity',
  'trigger',
  'surface',
  'premise',
])
const _MAX_TRANSCRIPT_ENTRIES = 20
const _MAX_ANSWER_LEN = 2048

export interface ImportNextQuestionInput {
  traceIds: string[]
  agentDefinition: string | null
  priorAnswers: PriorDiscoveryAnswer[]
  signal?: AbortSignal
}

export interface NextQuestionState {
  loaded: boolean
  next: NextDiscoveryQuestion | null
  done: boolean
  totalQuestions: number
  answeredCount: number
  error: string | null
}

export async function loadNextImportQuestion(
  input: ImportNextQuestionInput,
): Promise<NextQuestionState> {
  try {
    const resp: NextDiscoveryQuestionResponse = await fetchImportNextQuestion(
      input.traceIds,
      input.agentDefinition,
      input.priorAnswers,
      input.signal,
    )
    return {
      loaded: true,
      next: resp.next_question,
      done: resp.done,
      totalQuestions: resp.total_questions,
      answeredCount: resp.answered_count,
      error: null,
    }
  } catch (err) {
    const errMsg =
      err instanceof KernelApiError
        ? `Kernel error (${err.status}): ${err.detail}`
        : err instanceof Error
          ? err.message
          : String(err)
    return {
      loaded: false,
      next: null,
      done: false,
      totalQuestions: 0,
      answeredCount: 0,
      error: errMsg,
    }
  }
}

export interface ImportSummaryState {
  loaded: boolean
  summary: string | null
  basis: string | null
  source: string | null
  error: string | null
}

export async function loadImportSummary(
  traceIds: string[],
  agentDefinition: string | null,
): Promise<ImportSummaryState> {
  try {
    const resp = await fetchImportSummary(traceIds, agentDefinition)
    return {
      loaded: true,
      summary: resp.summary,
      basis: resp.basis,
      source: resp.source,
      error: null,
    }
  } catch (err) {
    const errMsg =
      err instanceof KernelApiError
        ? `Kernel error (${err.status}): ${err.detail}`
        : err instanceof Error
          ? err.message
          : String(err)
    return {
      loaded: false,
      summary: null,
      basis: null,
      source: null,
      error: errMsg,
    }
  }
}

export interface DiscoveryTranscriptEntry {
  entry_index: number
  question_index?: number | null
  dimension?: string | null
  kind?: string | null
  question: string
  answer: string | null
  chosen_option?: string | null
}

export interface GenerateFromImportInput {
  wsId: string
  traceIds: string[]
  agentDefinition: string | null
  origin: WorkflowOrigin | null
  reverseDiscovery: ReverseDiscoveryInput | null
  transcript: DiscoveryTranscriptEntry[]
}

export interface GenerateFromImportState {
  error: string | null
}

export async function generateFromImportAction(
  input: GenerateFromImportInput,
): Promise<GenerateFromImportState> {
  if (input.traceIds.length === 0) {
    return { error: 'No imported traces selected to generate from.' }
  }
  if (input.transcript.length > _MAX_TRANSCRIPT_ENTRIES) {
    return { error: 'Too many discovery answers — please reload and try again.' }
  }
  for (const entry of input.transcript) {
    if (entry.answer !== null && entry.answer.length > _MAX_ANSWER_LEN) {
      return { error: `Answer for question ${entry.question_index} exceeds the maximum length.` }
    }
    if (entry.kind != null && !_DISCOVERY_KINDS.has(entry.kind as DiscoveryQuestionKind)) {
      return { error: `Unknown question kind '${entry.kind}' — please reload and try again.` }
    }
  }

  const log = buildDesignAgentLog(input.transcript)

  let result
  try {
    result = await generateFromImport(
      input.traceIds,
      input.agentDefinition,
      log,
      input.reverseDiscovery,
      input.origin,
    )
  } catch (err) {
    if (err instanceof KernelApiError) {
      return { error: `Kernel error (${err.status}): ${err.detail}` }
    }
    return { error: err instanceof Error ? err.message : String(err) }
  }

  redirect(
    `/workspaces/${input.wsId}/workflows/new/review/${encodeURIComponent(result.workflow_id)}`,
  )
}

function buildDesignAgentLog(
  transcript: DiscoveryTranscriptEntry[],
): DesignAgentLog | null {
  if (transcript.length === 0) return null

  const entries: DesignAgentLogEntry[] = transcript.map((t, i) => ({
    question_index:
      typeof t.question_index === 'number' ? t.question_index : (t.entry_index ?? i),
    kind: (t.kind as DiscoveryQuestionKind) ?? 'ambiguity',
    question: t.question,
    answer: t.answer,
    dimension: (t.dimension as
      | import('@/lib/api').DesignDimension
      | undefined
      | null) ?? null,
    chosen_option: t.chosen_option ?? null,
  }))

  return {
    discovery_transcript: entries,
    ambiguity_report: null,
  }
}
