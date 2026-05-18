'use server'

import { redirect } from 'next/navigation'
import {
  fetchNextDiscoveryQuestion,
  generateWorkflow,
  KernelApiError,
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
const _MAX_AUGMENTED_LEN = 4096

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

// Stitch the discovery transcript onto the description before handing
// off to the existing NL-gen pipeline. The transcript becomes part of
// the spec's input record — the design-agent log column (slice 9.1.4)
// will mirror this into `audit_entries` once the migration ships.
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

  const augmented = withDiscoveryAppendix(description, input.transcript)
  if (augmented.length > _MAX_AUGMENTED_LEN) {
    return {
      error:
        'Your answers are too long to attach to the description (combined limit: 4096 characters). ' +
        'Shorten some answers or skip questions, then try again.',
    }
  }

  let result
  try {
    result = await generateWorkflow(
      augmented,
      undefined,
      input.templateId ?? undefined,
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

function withDiscoveryAppendix(
  description: string,
  transcript: DiscoveryTranscriptEntry[],
): string {
  const answered = transcript.filter((t) => t.answer !== null && t.answer.trim() !== '')
  if (answered.length === 0) return description
  const lines = answered.map(
    (t) => `- [${t.kind}] ${t.question}\n  → ${t.answer}`,
  )
  return [
    description,
    '',
    '## Design-agent discovery',
    ...lines,
  ].join('\n')
}
