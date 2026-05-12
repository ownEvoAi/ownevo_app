'use client'

import { useActionState, useState } from 'react'
import { useFormStatus } from 'react-dom'
import { generateWorkflowAction, type GenerateState } from './actions'

const initialState: GenerateState = { error: null }

export interface SampleDescription {
  id: string
  label: string
  description: string
}

export function NewWorkflowForm({
  wsId,
  samples,
}: {
  wsId: string
  samples: SampleDescription[]
}) {
  const action = generateWorkflowAction.bind(null, wsId)
  const [state, formAction] = useActionState(action, initialState)
  const [description, setDescription] = useState('')

  return (
    <form action={formAction} className="new-workflow-form">
      {samples.length > 0 ? (
        <div className="sample-row">
          <span className="sample-row-label">Try a sample:</span>
          {samples.map((s) => (
            <button
              key={s.id}
              type="button"
              className="sample-chip"
              onClick={() => setDescription(s.description)}
              title={s.description}
            >
              {s.label}
            </button>
          ))}
          {description.length > 0 ? (
            <button
              type="button"
              className="sample-chip-clear"
              onClick={() => setDescription('')}
            >
              Clear
            </button>
          ) : null}
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
        onChange={(e) => setDescription(e.target.value)}
        placeholder={
          'Recalibrate credit lines monthly across our 22,000-SMB portfolio. Flag accounts where utilization, DPD, or sector exposure suggest the line should be reduced. The chief risk officer reviews weekly. Past misses: we underweighted hospitality concentration in Q3 2024 and held lines too high through the spring rate-shock.'
        }
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
        <SubmitButton />
      </div>
    </form>
  )
}

function SubmitButton() {
  const { pending } = useFormStatus()
  return (
    <button type="submit" className="btn btn-primary" disabled={pending} aria-disabled={pending}>
      {pending ? (
        <>
          <span className="spinner" aria-hidden /> Generating…
        </>
      ) : (
        <>Generate &rsaquo;</>
      )}
    </button>
  )
}
