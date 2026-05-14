'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { updateDescriptionAction } from './actions'

// Client island for the inline description edit. Server Action runs
// the kernel PATCH; on success we router.refresh() so the layout
// title + sidebar pick up the new value without a page navigation.
export function DescriptionForm({
  wsId,
  wfId,
  initialDescription,
}: {
  wsId: string
  wfId: string
  initialDescription: string
}) {
  const router = useRouter()
  const [isPending, startTransition] = useTransition()
  const [description, setDescription] = useState(initialDescription)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const dirty = description.trim() !== initialDescription.trim()

  function handleSave() {
    setError(null)
    setSaved(false)
    startTransition(async () => {
      const result = await updateDescriptionAction({
        wsId,
        wfId,
        description,
      })
      if (!result.ok) {
        setError(result.error)
        return
      }
      setSaved(true)
      router.refresh()
    })
  }

  function handleReset() {
    setDescription(initialDescription)
    setError(null)
    setSaved(false)
  }

  return (
    <div className="settings-card">
      <div className="settings-card-header">
        <h2 className="settings-card-title">Description</h2>
        <p className="settings-card-subtitle">
          The natural-language description the workflow was generated from.
          Editing this is cosmetic — it does NOT regenerate the spec, sim,
          eval cases, or metric. Use the New workflow flow if those need
          to change.
        </p>
      </div>

      <textarea
        value={description}
        onChange={(e) => {
          setDescription(e.target.value)
          if (saved) setSaved(false)
        }}
        disabled={isPending}
        rows={6}
        className="settings-textarea"
        placeholder="Describe what this workflow does in plain language..."
      />
      <div className="settings-textarea-meta">
        {description.length} chars · min 10
      </div>

      <div className="settings-card-actions">
        <button
          type="button"
          onClick={handleSave}
          disabled={isPending || !dirty}
          className="btn btn-primary"
        >
          {isPending ? 'Saving…' : 'Save changes'}
        </button>
        {dirty && !isPending ? (
          <button
            type="button"
            onClick={handleReset}
            className="btn btn-secondary"
          >
            Discard
          </button>
        ) : null}
        {saved && !dirty ? (
          <span className="settings-saved-cue">Saved.</span>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="settings-error">
          {error}
        </p>
      ) : null}
    </div>
  )
}
