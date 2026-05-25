'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'

type SourceKey = 'otel' | 'upload' | 'manual'

interface SourceDef {
  key: SourceKey
  title: string
  body: string
  tags: string[]
  status: 'wired' | 'planned'
}

const SOURCES: SourceDef[] = [
  {
    key: 'otel',
    title: 'OpenTelemetry endpoint',
    body: 'Point your existing OTel collector at our OTLP endpoint. Use the gen-ai semantic conventions. No code changes if you already have OTel.',
    tags: ['recommended', 'live stream'],
    status: 'wired',
  },
  {
    key: 'upload',
    title: 'Upload trace export',
    body: 'JSONL, OTel-protobuf, or LangSmith / Phoenix export. Good for one-shot analysis without wiring up live ingest.',
    tags: ['batch'],
    status: 'planned',
  },
  {
    key: 'manual',
    title: 'Describe the agent manually',
    body: 'Skip trace ingestion. Tell ownEvo what the agent does and we generate the spec + eval set from your description. The improvement loop runs the same way.',
    tags: ['wired today'],
    status: 'wired',
  },
]

// Step 1 client island — choose a trace source, then advance. For
// `otel` + `upload` we land on the trace-import discovery surface, which
// summarises the agent's ingested traces and runs the design interview
// over them; for `manual` we route back into the existing /workflows/new
// NL-gen flow with a `from=connect` query so the form shows a thin
// BYO-context header.
export function SourcePicker({ wsId }: { wsId: string }) {
  const router = useRouter()
  const [selected, setSelected] = useState<SourceKey>('manual')

  function advance() {
    if (selected === 'manual') {
      router.push(`/workspaces/${wsId}/workflows/new?from=connect`)
    } else {
      router.push(
        `/workspaces/${wsId}/workflows/connect/design?source=${selected}`,
      )
    }
  }

  return (
    <>
      <div className="config-card-title" style={{ marginBottom: 12 }}>
        Pick a trace source
      </div>
      <div className="source-grid">
        {SOURCES.map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() => setSelected(s.key)}
            className={`source-card${selected === s.key ? ' selected' : ''}`}
          >
            <div className="source-title">{s.title}</div>
            <div className="source-meta">{s.body}</div>
            <div className="source-tags">
              {s.tags.map((t) => (
                <span key={t} className="source-tag">
                  {t}
                </span>
              ))}
              {s.status === 'planned' ? (
                <span className="source-tag source-tag-planned">
                  not wired yet
                </span>
              ) : null}
            </div>
          </button>
        ))}
      </div>

      <div className="connect-step-footer">
        <button
          type="button"
          onClick={advance}
          className="btn btn-primary"
          style={{ padding: '8px 16px', fontSize: 13 }}
        >
          {selected === 'manual'
            ? 'Continue with manual description →'
            : 'Continue →'}
        </button>
      </div>
    </>
  )
}
