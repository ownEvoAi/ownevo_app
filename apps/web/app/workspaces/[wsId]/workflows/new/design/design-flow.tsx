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
  const [isFetching, startFetch] = useTransition()
  const [isGenerating, startGenerate] = useTransition()
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  // Keep input focused as the conversation advances so the operator can
  // type or hit Skip without mousing back to the field.
  useEffect(() => {
    if (questionState.next) {
      inputRef.current?.focus()
    }
  }, [questionState.next?.question_index])

  const submitAnswer = (rawAnswer: string | null) => {
    const current = questionState.next
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

    startFetch(async () => {
      const resp = await loadNextQuestion({
        description,
        templateId,
        priorAnswers: nextTranscript.map((t) => ({
          question_index: t.question_index,
          answer: t.answer,
        })),
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

  const current = questionState.next
  const total = questionState.totalQuestions
  // answered_count comes from the kernel and reflects the prior_answers
  // length. Use the local transcript so the right-pane progress jumps
  // immediately on submit, even before the next-question fetch resolves.
  const answered = transcript.length
  const percent = total > 0 ? Math.round((answered / total) * 100) : 0
  const draftIsEmpty = draft.trim().length === 0

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

          {questionState.done ? (
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
                    disabled={isFetching}
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
              disabled={isFetching}
            />
            <div className="chat-composer-actions">
              <button
                type="button"
                className="btn btn-secondary chat-skip"
                onClick={() => submitAnswer(null)}
                disabled={isFetching}
              >
                Skip
              </button>
              <div className="gen-action-primary">
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isFetching || draftIsEmpty}
                  aria-disabled={isFetching || draftIsEmpty}
                >
                  {isFetching ? 'Loading…' : 'Send answer ›'}
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
            disabled={!questionState.done || isGenerating}
            aria-disabled={!questionState.done || isGenerating}
            onClick={generate}
          >
            {isGenerating ? 'Generating spec — ~30s' : 'Generate ›'}
          </button>
        </div>
        {!questionState.done ? (
          <p className="design-generate-hint">
            Generate unlocks after the discovery interview finishes.
            Skip remaining questions to unlock now.
          </p>
        ) : null}
      </aside>
    </div>
  )
}

// Silence "unused import" if startTransition gets eliminated by a future
// refactor; React 19 splits transitions vs useTransition and we keep
// both available.
void startTransition
