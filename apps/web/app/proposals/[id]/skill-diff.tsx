// Tiny line-level diff renderer for the proposal detail view.
//
// Real polish (W5) will use a Monaco-style side-by-side viewer. For
// W2.5 scaffold, a per-line classification (add/remove/context) is
// enough to prove the wire and the visual structure. We compute the
// classification server-side via an LCS so there's no client JS for
// reading.

interface DiffSegment {
  kind: 'add' | 'remove' | 'context'
  text: string
}

function lcsDiff(a: string[], b: string[]): DiffSegment[] {
  // Standard O(n*m) longest-common-subsequence; fine for skill bodies
  // (rarely >500 lines). Switch to Myers if a real workflow ever hits
  // multi-thousand-line skills.
  const n = a.length
  const m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array(m + 1).fill(0),
  )
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: DiffSegment[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'context', text: a[i] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'remove', text: a[i] })
      i++
    } else {
      out.push({ kind: 'add', text: b[j] })
      j++
    }
  }
  while (i < n) out.push({ kind: 'remove', text: a[i++] })
  while (j < m) out.push({ kind: 'add', text: b[j++] })
  return out
}

export function SkillDiff({
  current,
  proposed,
}: {
  current: string | null
  proposed: string
}) {
  // No parent version → just render the proposed body. The bootstrap
  // iteration of any workflow falls into this branch.
  if (current === null) {
    return (
      <div className="side">
        <div className="side-header">
          <span>Initial version</span>
          <span className="pill accent" style={{ fontSize: 9.5 }}>
            +{proposed.split('\n').length} lines
          </span>
        </div>
        <pre className="side-body code" style={codeBoxStyle}>
          <code>{proposed}</code>
        </pre>
      </div>
    )
  }

  const segments = lcsDiff(current.split('\n'), proposed.split('\n'))
  const adds = segments.filter((s) => s.kind === 'add').length
  const removes = segments.filter((s) => s.kind === 'remove').length

  return (
    <div className="side-by-side" style={{ display: 'grid', gap: 12 }}>
      <div className="side">
        <div className="side-header">
          <span>Proposed change</span>
          <span className="pill accent" style={{ fontSize: 9.5 }}>
            +{adds} · −{removes}
          </span>
        </div>
        <pre className="side-body code" style={codeBoxStyle}>
          {segments.map((seg, i) => (
            <span
              key={i}
              className={`diff-line ${
                seg.kind === 'add' ? 'diff-add' : seg.kind === 'remove' ? 'diff-del' : ''
              }`}
              style={{
                display: 'block',
                padding: '1px 8px',
                margin: '0 -8px',
                background:
                  seg.kind === 'add'
                    ? 'rgba(34, 197, 94, 0.10)'
                    : seg.kind === 'remove'
                      ? 'rgba(239, 68, 68, 0.10)'
                      : 'transparent',
                color:
                  seg.kind === 'context' ? 'var(--text-3)' : 'var(--text)',
              }}
            >
              {seg.kind === 'add' ? '+ ' : seg.kind === 'remove' ? '- ' : '  '}
              {seg.text || ' '}
            </span>
          ))}
        </pre>
      </div>
    </div>
  )
}

const codeBoxStyle: React.CSSProperties = {
  padding: '16px 18px',
  fontSize: 12.5,
  lineHeight: 1.65,
  fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace",
  margin: 0,
  whiteSpace: 'pre',
  overflowX: 'auto',
  background: 'var(--bg)',
  border: '1px solid var(--border)',
  borderRadius: 6,
}
