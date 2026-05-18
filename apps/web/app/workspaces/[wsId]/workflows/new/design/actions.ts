'use server'

import { redirect } from 'next/navigation'
import {
  fetchNextDiscoveryQuestion,
  generateWorkflow,
  KernelApiError,
  type NextDiscoveryQuestion,
  type NextDiscoveryQuestionResponse,
  type PriorDiscoveryAnswer,
} from '@/lib/api'

export interface NextQuestionInput {
  description: string
  templateId: string | null
  priorAnswers: PriorDiscoveryAnswer[]
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

  const augmented = withDiscoveryAppendix(description, input.transcript)

  try {
    const result = await generateWorkflow(
      augmented,
      undefined,
      input.templateId ?? undefined,
    )
    redirect(
      `/workspaces/${input.wsId}/workflows/new/review/${encodeURIComponent(
        result.workflow_id,
      )}`,
    )
  } catch (err) {
    // Next.js redirect() throws — let it propagate so the navigation
    // completes instead of being swallowed as an error banner.
    if (err instanceof Error && err.message === 'NEXT_REDIRECT') throw err
    if (err instanceof KernelApiError) {
      return { error: `Kernel error (${err.status}): ${err.detail}` }
    }
    return { error: err instanceof Error ? err.message : String(err) }
  }
  // unreachable — redirect threw
  return { error: null }
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
