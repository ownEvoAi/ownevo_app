'use client'

import { useRouter } from 'next/navigation'
import {
  useActionState,
  useRef,
  useState,
  useTransition,
  type KeyboardEvent,
} from 'react'
import { useFormStatus } from 'react-dom'
import { generateWorkflowAction, type GenerateState } from './actions'
import type { VerticalTemplate } from './templates'

const initialState: GenerateState = { error: null }

export function NewWorkflowForm({
  wsId,
  templates,
}: {
  wsId: string
  templates: VerticalTemplate[]
}) {
  const router = useRouter()
  const action = generateWorkflowAction.bind(null, wsId)
  const [state, formAction] = useActionState(action, initialState)
  const [description, setDescription] = useState('')
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(
    null,
  )
  const formRef = useRef<HTMLFormElement | null>(null)
  // `useTransition` lets us keep a pending flag while the design page is
  // server-rendering. The SSR pre-fetch makes one LLM call (Sonnet 4.6,
  // ~6–12s), and without a pending state the button looks frozen. Wrap
  // the router.push so the button can swap to a spinner + ETA copy.
  const [discoveryPending, startDiscoveryTransition] = useTransition()

  const runDiscovery = () => {
    const qs = new URLSearchParams()
    if (selectedTemplateId) qs.set('template_id', selectedTemplateId)
    if (description.trim()) qs.set('description', description.trim())
    const href = `/workspaces/${wsId}/workflows/new/design${
      qs.toString() ? `?${qs}` : ''
    }`
    startDiscoveryTransition(() => {
      router.push(href)
    })
  }
  const canRunDiscovery = description.trim().length >= 50 && !discoveryPending

  const pickTemplate = (t: VerticalTemplate) => {
    setSelectedTemplateId(t.id)
    setDescription(t.sample_description)
  }

  const clearTemplate = () => {
    const seed = templates.find((t) => t.id === selectedTemplateId)?.sample_description
    if (seed && description !== seed) {
      if (!window.confirm('Clear your edited description and start blank?')) return
    }
    setSelectedTemplateId(null)
    setDescription('')
  }

  // ⌘↵ / Ctrl-↵ from the textarea submits Generate without forcing the
  // reviewer to mouse over to the button. Browser default for ↵ in a
  // textarea is a newline, so we only intercept when a modifier is held.
  const onTextareaKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      formRef.current?.requestSubmit()
    }
  }

  return (
    <form ref={formRef} action={formAction} className="new-workflow-form">
      {templates.length > 0 ? (
        <div className="template-strip" role="group" aria-label="Starter templates">
          {templates.map((t) => {
            const active = t.id === selectedTemplateId
            return (
              <button
                key={t.id}
                type="button"
                className={`template-card${active ? ' active' : ''}`}
                onClick={() => pickTemplate(t)}
                aria-pressed={active}
              >
                <div className="template-card-name">{t.name}</div>
                <div className="template-card-tagline">{t.tagline}</div>
                <div className="template-card-persona">For: {t.persona}</div>
              </button>
            )
          })}
        </div>
      ) : null}

      {selectedTemplateId ? (
        <div className="template-attribution">
          <span>
            Started from <strong>{
              templates.find((t) => t.id === selectedTemplateId)?.name
            }</strong> template.
          </span>
          <button
            type="button"
            className="template-clear"
            onClick={clearTemplate}
          >
            Clear and start blank
          </button>
        </div>
      ) : null}

      <label className="new-workflow-label" htmlFor="description">
        Workflow description
      </label>
      <textarea
        id="description"
        name="description"
        className="new-workflow-textarea"
        rows={10}
        required
        minLength={50}
        maxLength={4096}
        value={description}
        onChange={(e) => {
          setDescription(e.target.value)
          // Editing the text after picking a template still counts as
          // template-attributed authoring — the user kept the starter
          // as a base. The "Start blank" button is how they opt out.
        }}
        onKeyDown={onTextareaKeyDown}
        placeholder={
          'Recalibrate credit lines monthly across our 22,000-SMB portfolio. Flag accounts where utilization, DPD, or sector exposure suggest the line should be reduced. The chief risk officer reviews weekly. Past misses: we underweighted hospitality concentration in Q3 2024 and held lines too high through the spring rate-shock.'
        }
      />

      <input
        type="hidden"
        name="template_id"
        value={selectedTemplateId ?? ''}
      />

      <details className="new-workflow-details">
        <summary>Advanced</summary>
        <label className="new-workflow-label" htmlFor="workflow_id">
          Workflow ID (optional — kebab-case slug)
        </label>
        <input
          id="workflow_id"
          name="workflow_id"
          type="text"
          className="new-workflow-input"
          pattern="^[a-z0-9][a-z0-9-]*[a-z0-9]$"
          placeholder="auto-derived from spec.id when blank"
        />
      </details>

      {state.error ? (
        <div role="alert" className="api-banner" style={{ marginTop: 12 }}>
          <strong>Generation failed.</strong> {state.error}
        </div>
      ) : null}

      <div className="gen-action-row">
        <a href={`/workspaces/${wsId}`} className="btn btn-secondary">
          &lsaquo; Cancel
        </a>
        <div className="gen-action-primary">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={runDiscovery}
            disabled={!canRunDiscovery}
            aria-disabled={!canRunDiscovery}
            aria-busy={discoveryPending}
            title={
              discoveryPending
                ? 'Starting the design agent — first question takes about 10s'
                : canRunDiscovery
                  ? 'Run a 1–2 minute discovery interview before generating'
                  : 'Write a description (50+ characters) first'
            }
          >
            {discoveryPending ? (
              <>
                <span className="spinner" aria-hidden /> Starting design
                agent — ~10s
              </>
            ) : (
              <>Design with agent &rsaquo;</>
            )}
          </button>
          <SubmitButton />
          <span className="kbd-hint">
            <kbd>⌘</kbd>
            <kbd>↵</kbd> to generate
          </span>
        </div>
      </div>
    </form>
  )
}

// NL-gen p50 from local dogfooding runs on Sonnet 4.6 / Sonnet 4.5:
// spec + simulation_plan + metric_definition land in 25-35 s for the
// three vertical templates. We surface ~30s as the visible estimate so
// the reviewer knows what "Generating" means, instead of an open-ended
// spinner. When we have a rolling avg from `iterations.duration_ms` we
// can read that from a config endpoint and replace this constant.
const NL_GEN_ETA_SECONDS = 30

function SubmitButton() {
  const { pending } = useFormStatus()
  return (
    <button type="submit" className="btn btn-primary" disabled={pending} aria-disabled={pending}>
      {pending ? (
        <>
          <span className="spinner" aria-hidden /> Generating spec — ~{NL_GEN_ETA_SECONDS}s
        </>
      ) : (
        <>Generate &rsaquo;</>
      )}
    </button>
  )
}
