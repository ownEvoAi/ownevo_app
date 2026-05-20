'use client'

import {
  startTransition,
  useEffect,
  useMemo,
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

// Dimension labels for the LLM path — the question carries `dimension`
// instead of `kind`. Falls through to a humanised kebab id when an
// unknown dimension shows up (e.g. a kernel rollout adds a new one
// before the web build catches up).
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
  return (
    DIMENSION_LABEL[q.dimension] || humaniseLabel(q.dimension) || 'Question'
  )
}

// Render order matches `DIMENSION_SPECS` in the kernel — earlier
// dimensions inform later ones (goal → trigger → connectors → metric →
// eval → UI → reviewer), so the strip reads left-to-right as the
// natural sequence even if the LLM jumps around.
const DIMENSION_ORDER: ReadonlyArray<keyof typeof DIMENSION_LABEL> = [
  'goal_and_scope',
  'trigger_and_cadence',
  'data_sources_and_connectors',
  'success_metric',
  'eval_seed_cases',
  'operate_ui_primitives',
  'reviewer_role',
]

function DimensionStrip({
  covered,
  currentDimension,
}: {
  covered: Set<string>
  currentDimension: string | null
}) {
  return (
    <div
      className="dimension-strip"
      role="list"
      aria-label="Discovery coverage"
    >
      {DIMENSION_ORDER.map((key) => {
        const state =
          covered.has(key)
            ? 'done'
            : currentDimension === key
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
  )
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
  //
  // `isLoadingFindings` intentionally NOT in deps: `startFindingsLoad`
  // flips it on, which would re-fire the effect, abort the in-flight
  // request, hit the AbortError early-return, and never set
  // `ambiguityLoaded` — looping forever. The single-load guard is
  // `ambiguityLoaded`, set inside the transition after the fetch
  // resolves (success path) or after a non-abort error (failure path).
  // Client-side retry for the initial question. The page's
  // server-side pre-fetch has a 5s timeout so the page never blocks
  // indefinitely on a slow kernel; the LLM-driven interviewer
  // commonly takes 6-12s, longer than the SSR budget. When the
  // initial state has no question + no error path, fire the fetch
  // again from the client without the timeout cap. Runs once per
  // page load thanks to the empty deps + the `tried.current` guard.
  const initialRetryTried = useRef(false)
  useEffect(() => {
    if (initialRetryTried.current) return
    if (questionState.loaded && (questionState.next || questionState.done)) {
      return
    }
    initialRetryTried.current = true
    startFetch(async () => {
      const resp = await loadNextQuestion({
        description,
        templateId,
        priorAnswers: [],
      })
      setQuestionState(resp)
    })
  }, [questionState.loaded, questionState.next, questionState.done, description, templateId])

  useEffect(() => {
    if (!questionState.done || ambiguityLoaded) return
    const controller = new AbortController()
    startFindingsLoad(async () => {
      try {
        const resp = await fetchDescriptionConflicts(description, controller.signal)
        setAmbiguityFindings(resp.findings)
        setAmbiguityError(null)
        setAmbiguityLoaded(true)
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
        setAmbiguityLoaded(true)
      }
    })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionState.done, ambiguityLoaded, description])

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

    // Detect ambiguity-finding questions by index range: kernel-side
    // LLM-driven questions don't carry a question_index (it's optional/
    // 0); ambiguity findings synthesised client-side use question_index
    // >= staticTotal. The fallback (legacy) path uses 0..staticTotal-1.
    const qIdx = current.question_index ?? null
    const isAmbiguityFinding =
      qIdx !== null && qIdx >= staticTotal

    const entryIndex = transcript.length
    const nextTranscript: DiscoveryTranscriptEntry[] = [
      ...transcript,
      {
        entry_index: entryIndex,
        question_index: qIdx,
        dimension: current.dimension ?? null,
        kind: current.kind ?? null,
        question: current.question,
        answer,
        chosen_option: answer,
      },
    ]

    setTranscript(nextTranscript)
    setDraft('')

    if (isAmbiguityFinding) {
      // Finding answers are local-only — no kernel round-trip, no echo
      // back into `prior_answers` (the synthetic indices would 400 the
      // static `/next-question` endpoint). Just advance the local
      // pointer; the answer rides into `discovery_transcript` on
      // Generate.
      setAmbiguityIndex((prev) => prev + 1)
      return
    }

    startFetch(async () => {
      // Echo every non-finding answer back to the kernel. The LLM path
      // uses `dimension` for coverage tracking; the fallback path uses
      // `question_index`. We pass both so either backend works.
      const priorAnswers = nextTranscript
        .filter(
          (t) =>
            t.question_index === null ||
            t.question_index === undefined ||
            t.question_index < staticTotal,
        )
        .map((t) => ({
          dimension: (t.dimension ?? null) as
            | import('@/lib/api').DesignDimension
            | null,
          question: t.question,
          chosen_option: t.chosen_option ?? null,
          free_text: t.answer,
          question_index: t.question_index ?? null,
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
  const coveredDimensions = useMemo(() => {
    const s = new Set<string>()
    for (const t of transcript) {
      if (t.dimension) s.add(t.dimension)
    }
    return s
  }, [transcript])
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

      {/* Centre pane — decision-brief panel. */}
      <section className="design-pane design-pane-centre" aria-label="Discovery interview">
        <h2 className="design-pane-title">Discovery</h2>

        {/* Dimension coverage strip: shows the seven design-shaping
            dimensions and which ones the interview has touched. Each
            chip flips done → current → pending based on the transcript
            + the current question's dimension. */}
        <DimensionStrip
          covered={coveredDimensions}
          currentDimension={current?.dimension ?? null}
        />

        {/* Past Q&A as compact one-liners (replaces big chat bubbles). */}
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

        {/* Current question — full decision brief. */}
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
              <span
                className="decision-brief-source"
                data-source={current.source}
              >
                {current.source === 'llm'
                  ? '· generated by design agent'
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
              <div
                className="option-cards"
                role="group"
                aria-label="Answer options"
              >
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
                          <span className="option-card-badge">
                            Recommended
                          </span>
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

            <form
              className="chat-composer"
              onSubmit={onSubmit}
              style={{ marginTop: 16 }}
            >
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

        {questionState.done && !ambiguityLoaded && !ambiguityError ? (
          <div className="chat-bubble chat-bubble-system">
            Scanning the description for ambiguities…
          </div>
        ) : null}

        {pendingFinding && ambiguityIndex === 0 ? (
          <div className="chat-bubble chat-bubble-system">
            I spotted {ambiguityFindings.length} ambiguity
            {ambiguityFindings.length === 1 ? '' : 's'} in the description.
            Let&rsquo;s resolve {ambiguityFindings.length === 1 ? 'it' : 'them'} before generating.
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
          {transcript.map((t, i) => {
            const label =
              (t.kind && KIND_LABEL[t.kind]) ||
              (t.dimension && DIMENSION_LABEL[t.dimension]) ||
              humaniseLabel(t.dimension) ||
              'Question'
            return (
              <li
                key={`${t.entry_index ?? i}`}
                className="design-transcript-item"
              >
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
    dimension: 'goal_and_scope',
    source: 'fallback',
    question: finding.suggested_question,
    eli: finding.summary,
    stakes:
      'Leaving the ambiguity unresolved makes the generated eval cases guess at the operator\'s intent.',
    options: [],
    recommendation_index: 0,
    rationale: finding.summary,
  }
}

// Silence "unused import" if startTransition gets eliminated by a future
// refactor; React 19 splits transitions vs useTransition and we keep
// both available.
void startTransition
