import type { DocBlock, DocumentData } from './types'
import { CaseCaption } from './case-caption'

interface Props {
  data: DocumentData
}

function renderBlock(b: DocBlock, idx: number): React.ReactNode {
  const text = b.text
  if (!b.spans || b.spans.length === 0) {
    if (b.kind === 'heading') return <h3 key={idx}>{text}</h3>
    if (b.kind === 'clause') {
      return (
        <div className="clause" key={idx}>
          {text}
        </div>
      )
    }
    return <p key={idx}>{text}</p>
  }
  const sorted = [...b.spans].sort((a, b) => a.start - b.start)
  const parts: React.ReactNode[] = []
  let cursor = 0
  for (const s of sorted) {
    if (s.start > cursor) parts.push(text.slice(cursor, s.start))
    parts.push(
      <span
        key={`${s.start}-${s.end}`}
        style={
          s.kind === 'flagged'
            ? {
                background: 'var(--amber-soft)',
                borderBottom: '2px solid var(--amber)',
                cursor: 'pointer',
              }
            : {
                background: 'var(--accent-muted)',
                borderBottom: '2px solid var(--accent)',
                cursor: 'pointer',
              }
        }
        title={s.note}
      >
        {text.slice(s.start, s.end)}
      </span>,
    )
    cursor = s.end
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  if (b.kind === 'heading') return <h3 key={idx}>{parts}</h3>
  if (b.kind === 'clause') {
    return (
      <div className="clause" key={idx}>
        {parts}
      </div>
    )
  }
  return <p key={idx}>{parts}</p>
}

export function DocumentReader({ data }: Props) {
  return (
    <div>
    <div className="doc-reader">
      <div className="doc-body">
        {data.section_label ? (
          <div
            style={{
              fontSize: 11,
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: 10,
            }}
          >
            {data.section_label}
          </div>
        ) : null}
        {data.blocks.map((b, i) => renderBlock(b, i))}
      </div>
      <div className="doc-margin">
        <div className="doc-margin-title">
          Annotations · {data.annotations.length}
        </div>
        {data.annotations.map((a) => (
          <div
            className="doc-annotation"
            key={a.id}
            style={{
              borderLeft: `3px solid var(--${
                a.severity === 'high' ? 'red' : a.severity === 'medium' ? 'amber' : 'accent'
              })`,
            }}
          >
            <div
              className="doc-annotation-title"
              style={{
                color: `var(--${
                  a.severity === 'high' ? 'red' : a.severity === 'medium' ? 'amber' : 'accent'
                })`,
              }}
            >
              {a.title}
            </div>
            <div className="doc-annotation-body">{a.body}</div>
          </div>
        ))}
      </div>
    </div>
    <CaseCaption caption={data.caption} />
    </div>
  )
}
