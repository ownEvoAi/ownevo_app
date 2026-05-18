'use client'

import {
  startTransition,
  useEffect,
  useRef,
  useState,
  useTransition,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import {
  fetchDescriptionConflicts,
  KernelApiError,
  type AmbiguityFinding,
  type NextDiscoveryQuestion,
} from '@/lib/api'
import {
  type DiscoveryTranscriptEntry,
  generateWithDiscoveryAction,
  loadNextQuestion,
  type NextQuestionState,
} from './actions'

interface DesignFlowProps {
  wsId: string
  description: string
  templateId: string | null
  templateName: string | null
  templatePersona: string | null
  initialState: NextQuestionState
}

// Friendly labels for the kind chip. Keep alphabetical by kind so the
// switch is easy to scan; CSS colour-codes via the data-kind attribute.
const KIND_LABEL: Record<string, string> = {
  ambiguity: 'Ambiguity',
  metric: 'Metric',
  premise: 'Premise',
  surface: 'Surface',
  trigger: 'Trigger',
}

export function DesignFlow({
  wsId,
  description,
  templateId,
  templateName,
  templatePersona,
  initialState,
}: DesignFlowProps) {
  const [questionState, setQuestionState] = useState<NextQuestionState>(initialState)
  const [transcript, setTranscript] = useState<DiscoveryTranscriptEntry[]>([])
  const [draft, setDraft] = useState('')
  const [generateError, setGenerateError] = useState<string | null>(null)
  // Pre-generation ambiguity findings. The static discovery interview
  // is the kernel's `next-question` walk over the prompt registry; once
  // it returns done=true we run `find_description_conflicts` over the
  // raw description and surface each finding as an additional question
  // in the chat. Finding answers ride into `discovery_transcript`
  // alongside the static answers; the kernel side never sees the
  // synthetic indices.
  const [ambiguityFindings, setAmbiguityFindings] = useState<AmbiguityFinding[]>([])
  const [ambiguityIndex, setAmbiguityIndex] = useState(0)
  const [ambiguityLoaded, setAmbiguityLoaded] = useState(false)
  const [ambiguityError, setAmbiguityError] = useState<string | null>(null)
  const [isFetching, startFetch] = useTransition()
  const [isLoadingFindings, startFindingsLoad] = useTransition()
  const [isGenerating, startGenerate] = useTransition()
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  // Once the static discovery interview returns done=true, fire the
  // pre-generation conflict scan exactly once. Called directly (not via
  // a server action) so the AbortController can cancel the in-flight
  // fetch on cleanup — prevents double-POST in React Strict Mode.
  useEffect(() => {
    if (!questionState.done || ambiguityLoaded || isLoadingFindings) return
    const controller = new AbortController()
    startFindingsLoad(async () => {
      try {
        const resp = await fetchDescriptionConflicts(description, controller.signal)
        setAmbiguityFindings(resp.findings)
        setAmbiguityError(null)
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        const errMsg =
          err instanceof KernelApiError
            ? `Kernel error (${err.status}): ${err.detail}`
            : err instanceof Error
              ? err.message
              : String(err)
        setAmbiguityFindings([])
        setAmbiguityError(errMsg)
      }
      setAmbiguityLoaded(true)
    })
    return () => controller.abort()
  }, [questionState.done, ambiguityLoaded, isLoadingFindings, description])

  // Total questions to surface in the chat = static count + findings
  // loaded so far. Before the findings load we display only the static
  // total so the progress bar does not jump around.
  const staticTotal = questionState.totalQuestions
  const totalQuestions = staticTotal + (ambiguityLoaded ? ambiguityFindings.length : 0)

  // The next pending finding (if any) surfaced as a synthetic
  // NextDiscoveryQuestion so the rest of the chat UI can render it
  // through the existing path without special-casing.
  const pendingFinding: NextDiscoveryQuestion | null =
    questionState.done &&
    ambiguityLoaded &&
    ambiguityIndex < ambiguityFindings.length
      ? findingToQuestion(
          ambiguityFindings[ambiguityIndex],
          ambiguityIndex,
          staticTotal,
        )
      : null

  const currentIsFinding = questionState.next === null && pendingFinding !== null
  const displayedCurrent: NextDiscoveryQuestion | null =
    questionState.next ?? pendingFinding

  // Keep input focused as the conversation advances so the operator can
  // type or hit Skip without mousing back to the field.
  useEffect(() => {
    if (displayedCurrent) {
      inputRef.current?.focus()
    }
  }, [displayedCurrent?.question_index])

  const submitAnswer = (rawAnswer: string | null) => {
    const current = displayedCurrent
    if (!current) return
    const answer =
      rawAnswer === null
        ? null
        : rawAnswer.trim() === ''
          ? null
          : rawAnswer.trim()

    const nextTranscript: DiscoveryTranscriptEntry[] = [
      ...transcript,
      {
        question_index: current.question_index,
        kind: current.kind,
        question: current.question,
        answer,
      },
    ]

    setTranscript(nextTranscript)
    setDraft('')

    if (currentIsFinding) {
      // Finding answers are local-only — no kernel round-trip, no echo
      // back into `prior_answers` (the synthetic indices would 400 the
      // static `/next-question` endpoint). Just advance the local
      // pointer; the answer rides into `discovery_transcript` on
      // Generate.
      setAmbiguityIndex((prev) => prev + 1)
      return
    }

    startFetch(async () => {
      // Static-question answers are echoed back so the kernel can
      // return the next not-yet-answered prompt. Only entries with
      // question_index < staticTotal are eligible — finding answers
      // carry synthetic indices above that range and are local-only.
      const priorAnswers = nextTranscript
        .filter((t) => t.question_index < staticTotal)
        .map((t) => ({
          question_index: t.question_index,
          answer: t.answer,
        }))
      const resp = await loadNextQuestion({
        description,
        templateId,
        priorAnswers,
      })
      setQuestionState(resp)
    })
  }

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    submitAnswer(draft)
  }

  // ⌘↵ / Ctrl-↵ inside the textarea submits the current draft, matching
  // the keyboard shortcut on /workflows/new. Plain Enter inserts a
  // newline (default textarea behaviour).
  const onTextareaKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      submitAnswer(draft)
    }
  }

  const generate = () => {
    setGenerateError(null)
    startGenerate(async () => {
      const result = await generateWithDiscoveryAction({
        wsId,
        description,
        templateId,
        transcript,
      })
      if (result.error) {
        setGenerateError(result.error)
      }
    })
  }

  const current = displayedCurrent
  const total = totalQuestions
  // answered_count comes from the kernel and reflects the prior_answers
  // length. Use the local transcript so the right-pane progress jumps
  // immediately on submit, even before the next-question fetch resolves.
  const answered = transcript.length
  const percent = total > 0 ? Math.round((answered / total) * 100) : 0
  const draftIsEmpty = draft.trim().length === 0
  const allFindingsAddressed =
    ambiguityLoaded && ambiguityIndex >= ambiguityFindings.length
  const discoveryDone = questionState.done && allFindingsAddressed
  const composerBusy = isFetching || isLoadingFindings

  return (
    <div className="design-grid">
      {/* Left pane — read-only description + template card. */}
      <aside className="design-pane design-pane-left">
        <h2 className="design-pane-title">Starting point</h2>
        {templateName ? (
          <div className="design-template-card" aria-label="Template">
            <div className="design-template-name">{templateName}</div>
            {templatePersona ? (
              <div className="design-template-persona">
                For: {templatePersona}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="design-template-card design-template-card-generic">
            <div className="design-template-name">Free-form description</div>
            <div className="design-template-persona">
              Generic discovery prompts
            </div>
          </div>
        )}
        <div className="design-description" aria-label="Workflow description">
          {description}
        </div>
      </aside>

      {/* Centre pane — the chat panel. */}
      <section className="design-pane design-pane-centre" aria-label="Discovery chat">
        <h2 className="design-pane-title">Discovery</h2>
        <div
          className="chat-history"
          role="log"
          aria-live="polite"
          aria-relevant="additions"
        >
          {transcript.map((entry, i) => (
            <div className="chat-pair" key={`${entry.question_index}-${i}`}>
              <div className="chat-bubble chat-bubble-agent">
                <div className="chat-bubble-kind" data-kind={entry.kind}>
                  {KIND_LABEL[entry.kind] ?? entry.kind}
                </div>
                <div className="chat-bubble-text">{entry.question}</div>
              </div>
              <div
                className={`chat-bubble chat-bubble-user${
                  entry.answer === null ? ' chat-bubble-skipped' : ''
                }`}
              >
                <div className="chat-bubble-text">
                  {entry.answer ?? 'Skipped'}
                </div>
              </div>
            </div>
          ))}

          {current ? (
            <div className="chat-pair chat-pair-current">
              <div className="chat-bubble chat-bubble-agent">
                <div className="chat-bubble-kind" data-kind={current.kind}>
                  {KIND_LABEL[current.kind] ?? current.kind}
                </div>
                <div className="chat-bubble-text">{current.question}</div>
                {current.rationale ? (
                  <div className="chat-bubble-rationale">
                    Why I&rsquo;m asking: {current.rationale}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {questionState.done && !ambiguityLoaded && !ambiguityError ? (
            <div className="chat-bubble chat-bubble-system">
              Scanning the description for ambiguities…
            </div>
          ) : null}

          {pendingFinding && ambiguityIndex === 0 ? (
            <div className="chat-bubble chat-bubble-system">
              I spotted {ambiguityFindings.length} ambiguity
              {ambiguityFindings.length === 1 ? '' : 's'} in the
              description. Let&rsquo;s resolve {ambiguityFindings.length === 1 ? 'it' : 'them'} before generating.
            </div>
          ) : null}

          {discoveryDone ? (
            <div className="chat-bubble chat-bubble-system">
              Discovery complete. Review the answers on the right, then
              click <strong>Generate</strong>.
            </div>
          ) : null}

          {questionState.error ? (
            <div role="alert" className="api-banner">
              <strong>Discovery failed.</strong> {questionState.error}
            </div>
          ) : null}

          {ambiguityError ? (
            <div role="alert" className="api-banner">
              <strong>Ambiguity scan failed.</strong> {ambiguityError}
            </div>
          ) : null}
        </div>

        {current ? (
          <form className="chat-composer" onSubmit={onSubmit}>
            {current.options && current.options.length > 0 ? (
              <div className="chat-options" role="group" aria-label="Answer options">
                {current.options.map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    className="chat-option-chip"
                    onClick={() => submitAnswer(opt)}
                    disabled={composerBusy}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            ) : null}
            <label className="sr-only" htmlFor="discovery-answer">
              Your answer
            </label>
            <textarea
              id="discovery-answer"
              ref={inputRef}
              className="chat-input"
              rows={2}
              maxLength={2048}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onTextareaKeyDown}
              placeholder={
                current.options && current.options.length > 0
                  ? 'Or type your own answer…'
                  : 'Type your answer…'
              }
              disabled={composerBusy}
            />
            <div className="chat-composer-actions">
              <button
                type="button"
                className="btn btn-secondary chat-skip"
                onClick={() => submitAnswer(null)}
                disabled={composerBusy}
              >
                Skip
              </button>
              <div className="gen-action-primary">
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={composerBusy || draftIsEmpty}
                  aria-disabled={composerBusy || draftIsEmpty}
                >
                  {composerBusy ? 'Loading…' : 'Send answer ›'}
                </button>
                <span className="kbd-hint">
                  <kbd>⌘</kbd>
                  <kbd>↵</kbd> to send
                </span>
              </div>
            </div>
          </form>
        ) : null}
      </section>

      {/* Right pane — transcript + Generate. */}
      <aside className="design-pane design-pane-right">
        <h2 className="design-pane-title">Progress</h2>
        <div className="design-progress" aria-label="Discovery progress">
          <div className="design-progress-meter">
            <div
              className="design-progress-fill"
              style={{ width: `${percent}%` }}
              aria-hidden
            />
          </div>
          <div className="design-progress-label">
            {answered} of {total > 0 ? total : '—'} answered
          </div>
        </div>

        <ol className="design-transcript">
          {transcript.length === 0 ? (
            <li className="design-transcript-empty">
              No answers yet.
            </li>
          ) : null}
          {transcript.map((t, i) => (
            <li
              key={`${t.question_index}-${i}`}
              className="design-transcript-item"
            >
              <span
                className="design-transcript-kind"
                data-kind={t.kind}
                aria-hidden
              >
                {KIND_LABEL[t.kind] ?? t.kind}
              </span>
              <span className="design-transcript-answer">
                {t.answer ?? <em>Skipped</em>}
              </span>
            </li>
          ))}
        </ol>

        {generateError ? (
          <div role="alert" className="api-banner">
            <strong>Generation failed.</strong> {generateError}
          </div>
        ) : null}

        <div className="gen-action-row">
          <button
            type="button"
            className="btn btn-primary design-generate"
            disabled={!discoveryDone || isGenerating}
            aria-disabled={!discoveryDone || isGenerating}
            onClick={generate}
          >
            {isGenerating ? 'Generating spec — ~30s' : 'Generate ›'}
          </button>
        </div>
        {!discoveryDone ? (
          <p className="design-generate-hint">
            {!questionState.done
              ? 'Generate unlocks after the discovery interview finishes. Skip remaining questions to unlock now.'
              : !ambiguityLoaded
                ? 'Scanning the description for ambiguities before unlocking Generate.'
                : 'Address the flagged ambiguities to unlock Generate. Skip any you cannot answer.'}
          </p>
        ) : null}
      </aside>
    </div>
  )
}

// Wraps a pre-generation AmbiguityFinding in the same NextDiscoveryQuestion
// shape the chat panel already renders for static prompt-library questions.
// `question_index` continues past the static range so the answer entry in
// `discovery_transcript` carries a unique handle for the audit trail. The
// finding's `summary` rides along as `rationale` so the operator sees the
// "why I'm asking" line under the question.
function findingToQuestion(
  finding: AmbiguityFinding,
  index: number,
  staticTotal: number,
): NextDiscoveryQuestion {
  return {
    question_index: staticTotal + index,
    kind: 'ambiguity',
    question: finding.suggested_question,
    options: null,
    rationale: finding.summary,
  }
}

// Silence "unused import" if startTransition gets eliminated by a future
// refactor; React 19 splits transitions vs useTransition and we keep
// both available.
void startTransition
