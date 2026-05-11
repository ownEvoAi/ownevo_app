import type { SideBySideData, SidePanel } from './types'

interface Props {
  data: SideBySideData
}

// Render a panel body with span-based highlights so inline `added` /
// `removed` ranges visibly diff without a separate line-level diff
// engine (skill-diff.tsx handles that case; this is the inline-mark
// variant for clause-level comparison).
function renderBody(panel: SidePanel) {
  const text = panel.body
  if (!panel.highlights || panel.highlights.length === 0) {
    return text
  }
  const sorted = [...panel.highlights].sort((a, b) => a.start - b.start)
  const out: React.ReactNode[] = []
  let cursor = 0
  for (const h of sorted) {
    const start = Math.max(cursor, h.start)
    const end = Math.min(text.length, h.end)
    if (start > cursor) out.push(text.slice(cursor, start))
    if (end > start) {
      const cls =
        h.kind === 'added' ? 'diff-add' : h.kind === 'removed' ? 'diff-del' : ''
      out.push(
        <span key={`${h.start}-${h.end}`} className={cls}>
          {text.slice(start, end)}
        </span>,
      )
    }
    cursor = Math.max(cursor, end)
  }
  if (cursor < text.length) out.push(text.slice(cursor))
  return out
}

export function SideBySideView({ data }: Props) {
  return (
    <div className="side-by-side">
      {[data.left, data.right].map((panel, i) => (
        <div className="side" key={i}>
          <div
            className="side-header"
            style={
              i === 1
                ? { background: 'var(--green-soft)', color: 'var(--green)' }
                : undefined
            }
          >
            <span>{panel.title}</span>
          </div>
          <div className={panel.format === 'code' ? 'side-body code' : 'side-body'}>
            {renderBody(panel)}
          </div>
        </div>
      ))}
    </div>
  )
}
