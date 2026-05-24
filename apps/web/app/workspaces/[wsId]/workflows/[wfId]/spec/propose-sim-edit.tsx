'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { createSimProposal, KernelApiError } from '@/lib/api'

interface Props {
  wsId: string
  wfId: string
  tools: ReadonlyArray<unknown>
  personas: ReadonlyArray<unknown>
  envGenerators: ReadonlyArray<unknown>
  dataSources: ReadonlyArray<unknown>
}

// 9.2.3 — Propose-edit affordance for the simulator on the workflow
// Spec tab. The sim plan is the most structurally complex artifact
// (tools, personas, env generators, data sources, each with nested
// inputs / props). Editing it cleanly is a multi-step UX — that
// editor is out of scope for this slice.
//
// First-cut UX: a JSON paste box pre-populated with the current sim
// sections. The reviewer edits the JSON, the form validates it
// parses + carries at least one sim section, and posts a kind='sim'
// proposal. The diff renderer on the proposal-detail page shows
// added/removed tools / personas / data sources / env generators by
// name so the impact is legible without reading the raw JSON.
export function ProposeSimEdit({
  wsId,
  wfId,
  tools,
  personas,
  envGenerators,
  dataSources,
}: Props) {
  const router = useRouter()
  const initial = JSON.stringify(
    {
      tools,
      personas,
      env_generators: envGenerators,
      data_sources: dataSources,
    },
    null,
    2,
  )
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(initial)
  const [summary, setSummary] = useState('')
  const [rationale, setRationale] = useState('')
  const [isPending, startTransition] = useTransition()
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setDraft(initial)
    setSummary('')
    setRationale('')
    setError(null)
  }

  function handleCancel() {
    setEditing(false)
    reset()
  }

  function handleSubmit() {
    setError(null)
    if (summary.trim().length === 0) {
      setError('Plain-language summary is required.')
      return
    }
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(draft)
    } catch (e) {
      setError(
        'Proposed agent environment is not valid JSON: ' +
          (e instanceof Error ? e.message : String(e)),
      )
      return
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setError('Proposed agent environment must be a JSON object.')
      return
    }
    startTransition(async () => {
      try {
        const proposal = await createSimProposal(wfId, {
          plain_language_summary: summary.trim(),
          proposed_spec: parsed,
          rationale: rationale.trim() || null,
        })
        setEditing(false)
        reset()
        router.refresh()
        router.push(`/workspaces/${wsId}/proposals/${proposal.id}`)
      } catch (err) {
        if (err instanceof KernelApiError) {
          setError(err.detail || err.message)
        } else {
          setError(err instanceof Error ? err.message : 'Unknown error')
        }
      }
    })
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="btn btn-secondary propose-edit-btn"
      >
        Propose edit
      </button>
    )
  }

  return (
    <div className="propose-edit-panel">
      <div className="propose-edit-head">
        <strong>Propose agent environment edit</strong>
        <span className="propose-edit-help">
          Edit the JSON below — tools / personas / data_sources /
          env_generators. This edits the agent&apos;s runtime
          environment, not the replay simulator that pins eval cases.
          Goes through proposal review + domain-expert approval.
        </span>
      </div>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={isPending}
        rows={16}
        spellCheck={false}
        className="propose-sim-textarea"
      />
      <label className="propose-edit-field">
        <span>
          Plain-language summary <em>(shown in the proposal queue)</em>
        </span>
        <input
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          placeholder="e.g. Add seasonal_index lookup tool to the agent."
          disabled={isPending}
        />
      </label>
      <label className="propose-edit-field">
        <span>
          Rationale <em>(optional · why this change)</em>
        </span>
        <textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={2}
          disabled={isPending}
        />
      </label>
      <div className="propose-edit-actions">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={isPending}
          className="btn btn-primary"
        >
          {isPending ? 'Creating…' : 'Create proposal'}
        </button>
        <button
          type="button"
          onClick={handleCancel}
          disabled={isPending}
          className="btn btn-secondary"
        >
          Cancel
        </button>
      </div>
      {error ? (
        <p role="alert" className="propose-edit-error">
          {error}
        </p>
      ) : null}
    </div>
  )
}
