'use server'

import { redirect } from 'next/navigation'
import {
  fetchNextDiscoveryQuestion,
  generateWorkflow,
  KernelApiError,
  type DesignAgentLog,
  type DesignAgentLogEntry,
  type DiscoveryQuestionKind,
  type NextDiscoveryQuestion,
  type NextDiscoveryQuestionResponse,
  type PriorDiscoveryAnswer,
} from '@/lib/api'

const _DISCOVERY_KINDS: ReadonlySet<DiscoveryQuestionKind> = new Set([
  'metric',
  'ambiguity',
  'trigger',
  'surface',
  'premise',
])
const _MAX_TRANSCRIPT_ENTRIES = 32
const _MAX_ANSWER_LEN = 2048

export interface NextQuestionInput {
  description: string
  templateId: string | null
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

export async function loadNextQuestion(
  input: NextQuestionInput,
): Promise<NextQuestionState> {
  try {
    const resp: NextDiscoveryQuestionResponse = await fetchNextDiscoveryQuestion(
      input.description,
      input.templateId,
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

export interface DiscoveryTranscriptEntry {
  question_index: number
  kind: string
  question: string
  answer: string | null
}

export interface GenerateWithDiscoveryInput {
  wsId: string
  description: string
  templateId: string | null
  transcript: DiscoveryTranscriptEntry[]
}

export interface GenerateWithDiscoveryState {
  error: string | null
}

// Send the discovery transcript as a structured `design_agent_log`
// field on POST /api/nl-gen/generate (PLAN 9.1.4). The kernel persists
// it on the `workflows.design_agent_log` JSONB column and mirrors each
// Q/A into `audit_entries` as a `design-agent-negotiation` row. The
// original description stays clean — no more appendix-stitching.
export async function generateWithDiscoveryAction(
  input: GenerateWithDiscoveryInput,
): Promise<GenerateWithDiscoveryState> {
  const description = input.description.trim()
  if (description.length < 50) {
    return { error: 'Description must be at least 50 characters.' }
  }

  if (input.transcript.length > _MAX_TRANSCRIPT_ENTRIES) {
    return { error: 'Too many discovery answers — please reload and try again.' }
  }
  for (const entry of input.transcript) {
    if (entry.answer !== null && entry.answer.length > _MAX_ANSWER_LEN) {
      return { error: `Answer for question ${entry.question_index} exceeds the maximum length.` }
    }
    if (!_DISCOVERY_KINDS.has(entry.kind as DiscoveryQuestionKind)) {
      return { error: `Unknown question kind '${entry.kind}' — please reload and try again.` }
    }
  }

  const log = buildDesignAgentLog(input.transcript)

  let result
  try {
    result = await generateWorkflow(
      description,
      undefined,
      input.templateId ?? undefined,
      log,
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

// Build the wire-shape DesignAgentLog from the client-side transcript.
// Returns null when the operator skipped every question — no need to
// send an empty log; the column stays NULL on the workflow row, which
// is what the kernel expects.
function buildDesignAgentLog(
  transcript: DiscoveryTranscriptEntry[],
): DesignAgentLog | null {
  if (transcript.length === 0) return null

  const entries: DesignAgentLogEntry[] = transcript.map((t) => ({
    question_index: t.question_index,
    kind: t.kind as DiscoveryQuestionKind,
    question: t.question,
    answer: t.answer,
  }))

  return {
    discovery_transcript: entries,
    ambiguity_report: null,
  }
}
