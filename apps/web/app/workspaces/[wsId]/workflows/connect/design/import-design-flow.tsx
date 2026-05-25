'use client'

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import type { NextDiscoveryQuestion } from '@/lib/api'
import {
  type DiscoveryTranscriptEntry,
  generateFromImportAction,
  loadNextImportQuestion,
  type NextQuestionState,
} from './actions'

interface ImportDesignFlowProps {
  wsId: string
  traceIds: string[]
  agentDefinition: string | null
  /** Pre-rendered observed-behaviour summary for the left pane. */
  traceSummaryLines: string[]
  traceCount: number
  initialState: NextQuestionState
}

const KIND_LABEL: Record<string, string> = {
  ambiguity: 'Ambiguity',
  metric: 'Metric',
  premise: 'Premise',
  surface: 'Surface',
  trigger: 'Trigger',
}

const DIMENSION_LABEL: Record<string, string> = {
  goal_and_scope: 'Goal & scope',
  trigger_and_cadence: 'Trigger & cadence',
  data_sources_and_connectors: 'Data sources',
  success_metric: 'Success metric',
  eval_seed_cases: 'Eval seed cases',
  operate_ui_primitives: 'Operate UI',
  reviewer_role: 'Reviewer',
}

function humaniseLabel(slug: string | null | undefined): string {
  if (!slug) return ''
  return slug
    .split(/[_-]/)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' ')
}

function questionKindLabel(q: NextDiscoveryQuestion): string {
  if (q.kind && KIND_LABEL[q.kind]) return KIND_LABEL[q.kind]
  return DIMENSION_LABEL[q.dimension] || humaniseLabel(q.dimension) || 'Question'
}

export function ImportDesignFlow({
  wsId,
  traceIds,
  agentDefinition,
  traceSummaryLines,
  traceCount,
  initialState,
}: ImportDesignFlowProps) {
  const [questionState, setQuestionState] = useState<NextQuestionState>(initialState)
  const [transcript, setTranscript] = useState<DiscoveryTranscriptEntry[]>([])
  const [draft, setDraft] = useState('')
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [isFetching, startFetch] = useTransition()
  const [isGenerating, startGenerate] = useTransition()
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  // Client-side retry for the initial question: the page's SSR pre-fetch
  // has a short budget, but the LLM interviewer commonly takes longer.
  // Fire again from the client (no timeout cap) when SSR returned nothing.
  const initialRetryTried = useRef(false)
  useEffect(() => {
    if (initialRetryTried.current) return
    if (questionState.loaded && (questionState.next || questionState.done)) return
    initialRetryTried.current = true
    startFetch(async () => {
      const resp = await loadNextImportQuestion({
        traceIds,
        agentDefinition,
        priorAnswers: [],
      })
      setQuestionState(resp)
    })
  }, [questionState.loaded, questionState.next, questionState.done, traceIds, agentDefinition])

  const current = questionState.next

  useEffect(() => {
    if (current) inputRef.current?.focus()
  }, [current?.question_index, current?.question])

  const submitAnswer = (rawAnswer: string | null) => {
    if (!current) return
    const answer =
      rawAnswer === null ? null : rawAnswer.trim() === '' ? null : rawAnswer.trim()

    const entryIndex = transcript.length
    const nextTranscript: DiscoveryTranscriptEntry[] = [
      ...transcript,
      {
        entry_index: entryIndex,
        question_index: current.question_index ?? null,
        dimension: current.dimension ?? null,
        kind: current.kind ?? null,
        question: current.question,
        answer,
        chosen_option: answer,
      },
    ]
    setTranscript(nextTranscript)
    setDraft('')

    startFetch(async () => {
      const priorAnswers = nextTranscript.map((t) => ({
        dimension: (t.dimension ?? null) as
          | import('@/lib/api').DesignDimension
          | null,
        question: t.question,
        chosen_option: t.chosen_option ?? null,
        free_text: t.answer,
        question_index: t.question_index ?? null,
        answer: t.answer,
      }))
      const resp = await loadNextImportQuestion({
        traceIds,
        agentDefinition,
        priorAnswers,
      })
      setQuestionState(resp)
    })
  }

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    submitAnswer(draft)
  }

  const onTextareaKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      submitAnswer(draft)
    }
  }

  const generate = () => {
    setGenerateError(null)
    startGenerate(async () => {
      const result = await generateFromImportAction({
        wsId,
        traceIds,
        agentDefinition,
        transcript,
      })
      if (result?.error) setGenerateError(result.error)
    })
  }

  const coveredDimensions = useMemo(() => {
    const s = new Set<string>()
    for (const t of transcript) if (t.dimension) s.add(t.dimension)
    return s
  }, [transcript])

  const total = questionState.totalQuestions
  const answered = transcript.length
  const percent = total > 0 ? Math.round((answered / total) * 100) : 0
  const draftIsEmpty = draft.trim().length === 0
  const discoveryDone = questionState.done
  const composerBusy = isFetching

  return (
    <div className="design-grid">
      {/* Left pane — imported agent: observed behaviour + definition. */}
      <aside className="design-pane design-pane-left">
        <h2 className="design-pane-title">Imported agent</h2>
        <div className="design-template-card" aria-label="Imported traces">
          <div className="design-template-name">
            {traceCount} imported trace{traceCount === 1 ? '' : 's'}
          </div>
          <div className="design-template-persona">Observed behaviour</div>
        </div>
        <div className="design-description" aria-label="Observed behaviour">
          {traceSummaryLines.map((line, i) => (
            <div key={i}>{line || ' '}</div>
          ))}
        </div>
        {agentDefinition ? (
          <>
            <h2 className="design-pane-title" style={{ marginTop: 16 }}>
              Agent definition
            </h2>
            <div className="design-description" aria-label="Agent definition">
              {agentDefinition}
            </div>
          </>
        ) : null}
      </aside>

      {/* Centre pane — decision-brief interview. */}
      <section
        className="design-pane design-pane-centre"
        aria-label="Discovery interview"
      >
        <h2 className="design-pane-title">Discovery</h2>

        <div className="dimension-strip" role="list" aria-label="Discovery coverage">
          {Object.keys(DIMENSION_LABEL).map((key) => {
            const state = coveredDimensions.has(key)
              ? 'done'
              : current?.dimension === key
                ? 'current'
                : 'pending'
            return (
              <span
                key={key}
                className="dimension-chip"
                data-state={state}
                role="listitem"
              >
                {DIMENSION_LABEL[key]}
              </span>
            )
          })}
        </div>

        {transcript.length > 0 ? (
          <ul className="past-qa-list" aria-label="Previous answers">
            {transcript.map((entry, i) => {
              const dimLabel =
                (entry.dimension && DIMENSION_LABEL[entry.dimension]) ||
                (entry.kind && KIND_LABEL[entry.kind]) ||
                humaniseLabel(entry.dimension ?? entry.kind ?? '') ||
                'Question'
              return (
                <li
                  key={`${entry.entry_index ?? i}`}
                  className="past-qa-item"
                  title={entry.question}
                >
                  <span className="past-qa-dimension">{dimLabel}</span>
                  <span className="past-qa-answer">
                    {entry.answer ?? <em>Skipped</em>}
                  </span>
                </li>
              )
            })}
          </ul>
        ) : null}

        {current ? (
          <article
            className="decision-brief"
            aria-live="polite"
            aria-label={`Question: ${questionKindLabel(current)}`}
          >
            <header className="decision-brief-header">
              <span className="decision-brief-dimension">
                {questionKindLabel(current)}
              </span>
              <span className="decision-brief-source" data-source={current.source}>
                {current.source === 'llm'
                  ? '· generated from your traces'
                  : '· template fallback'}
              </span>
            </header>

            <div className="decision-brief-question">{current.question}</div>

            {current.eli ? (
              <p className="decision-brief-eli">{current.eli}</p>
            ) : null}

            {current.stakes ? (
              <div className="decision-brief-stakes">
                <span className="decision-brief-stakes-label">Stakes</span>
                {current.stakes}
              </div>
            ) : null}

            {current.options && current.options.length > 0 ? (
              <div className="option-cards" role="group" aria-label="Answer options">
                {current.options.map((opt, i) => {
                  const isRecommended = i === current.recommendation_index
                  return (
                    <button
                      key={`${opt.label}-${i}`}
                      type="button"
                      className={`option-card${
                        isRecommended ? ' option-card-recommended' : ''
                      }`}
                      onClick={() => submitAnswer(opt.label)}
                      disabled={composerBusy}
                    >
                      <div className="option-card-header">
                        <span className="option-card-label">{opt.label}</span>
                        {isRecommended ? (
                          <span className="option-card-badge">Recommended</span>
                        ) : null}
                      </div>
                      {(opt.pro || opt.con) &&
                      !(
                        opt.pro === '(see rationale)' &&
                        opt.con === '(tradeoff not surfaced in fallback mode)'
                      ) ? (
                        <div className="option-card-prose">
                          {opt.pro ? (
                            <div className="option-card-pro">
                              <span>{opt.pro}</span>
                            </div>
                          ) : null}
                          {opt.con ? (
                            <div className="option-card-con">
                              <span>{opt.con}</span>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </button>
                  )
                })}
              </div>
            ) : null}

            {current.rationale ? (
              <div className="decision-brief-rationale">
                <strong>
                  Why{' '}
                  {current.options[current.recommendation_index]?.label
                    ? `"${current.options[current.recommendation_index].label}"`
                    : 'this'}{' '}
                  is the recommendation:
                </strong>{' '}
                {current.rationale}
              </div>
            ) : null}

            <form className="chat-composer" onSubmit={onSubmit} style={{ marginTop: 16 }}>
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
                    ? 'Or type a custom answer…'
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
          </article>
        ) : null}

        {discoveryDone ? (
          <div className="chat-bubble chat-bubble-system">
            Discovery complete. Review the answers on the right, then click{' '}
            <strong>Generate</strong> to attach the loop to this agent.
          </div>
        ) : null}

        {questionState.error ? (
          <div role="alert" className="api-banner">
            <strong>Discovery failed.</strong> {questionState.error}
          </div>
        ) : null}
      </section>

      {/* Right pane — progress + transcript + Generate. */}
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
            <li className="design-transcript-empty">No answers yet.</li>
          ) : null}
          {transcript.map((t, i) => {
            const label =
              (t.kind && KIND_LABEL[t.kind]) ||
              (t.dimension && DIMENSION_LABEL[t.dimension]) ||
              humaniseLabel(t.dimension) ||
              'Question'
            return (
              <li key={`${t.entry_index ?? i}`} className="design-transcript-item">
                <span
                  className="design-transcript-kind"
                  data-kind={t.kind ?? t.dimension ?? 'question'}
                  aria-hidden
                >
                  {label}
                </span>
                <span className="design-transcript-answer">
                  {t.answer ?? <em>Skipped</em>}
                </span>
              </li>
            )
          })}
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
            Generate unlocks after the discovery interview finishes. Skip
            remaining questions to unlock now.
          </p>
        ) : null}
      </aside>
    </div>
  )
}
