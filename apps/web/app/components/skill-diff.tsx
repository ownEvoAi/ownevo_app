// — true side-by-side diff. Two columns: current (left) vs.
// proposed (right). Each side renders the full skill body for that
// version, with mismatched lines highlighted (`diff-del` on the left,
// `diff-add` on the right) and shared lines as `ctx`. Classes are
// already defined in `public/styles/primitives.css` — same set the
// `07-proposal-detail.html` mock uses.
//
// We keep the LCS computation (rare >500-line skills, fine) and emit
// per-row classifications for both sides in lockstep. Pure server
// component — zero client JS.

interface DiffSegment {
 kind: 'add' | 'remove' | 'context'
 text: string
}

interface SidePart {
 kind: 'context' | 'diff'
 text: string
}

function lcsDiff(a: string[], b: string[]): DiffSegment[] {
 const n = a.length
 const m = b.length
 const dp: number[][] = Array.from({ length: n + 1 }, =>
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

// Skills beyond this line count trigger a single-column fallback to avoid
// O(n×m) LCS memory pressure on the Next.js server process.
const MAX_LCS_LINES = 500

function partition(segments: DiffSegment[]): {
 left: SidePart[]
 right: SidePart[]
 adds: number
 removes: number
} {
 const left: SidePart[] = []
 const right: SidePart[] = []
 let adds = 0
 let removes = 0
 for (const s of segments) {
 if (s.kind === 'context') {
 left.push({ kind: 'context', text: s.text })
 right.push({ kind: 'context', text: s.text })
 } else if (s.kind === 'remove') {
 left.push({ kind: 'diff', text: s.text })
 removes++
 } else {
 right.push({ kind: 'diff', text: s.text })
 adds++
 }
 }
 return { left, right, adds, removes }
}

export function SkillDiff({
 current,
 proposed,
 parentVersionSeq,
}: {
 current: string | null
 proposed: string
 parentVersionSeq: number | null
}) {
 if (current === null) {
 // Bootstrap iteration — no parent version. Show a single column
 // with the full proposed body.
 return (
 <div className="side-by-side" style={{ gridTemplateColumns: '1fr' }}>
 <div className="side">
 <div className="side-header">
 <span>Initial version</span>
 <span className="pill accent" style={{ fontSize: 9.5 }}>
 +{proposed.split('\n').length} lines
 </span>
 </div>
 <pre className="side-body code" style={codeBoxStyle}>
 {proposed.split('\n').map((line, i) => (
 <Line key={i} kind="diff" side="right" text={line} />
 ))}
 </pre>
 </div>
 </div>
 )
 }

 const currentLines = current.split('\n')
 const proposedLines = proposed.split('\n')

 if (currentLines.length > MAX_LCS_LINES || proposedLines.length > MAX_LCS_LINES) {
 return (
 <div className="side-by-side" style={{ gridTemplateColumns: '1fr' }}>
 <div className="side">
 <div className="side-header">
 <span>Proposed · {proposedLines.length} lines (diff omitted — too large)</span>
 </div>
 <pre className="side-body code" style={codeBoxStyle}>
 {proposedLines.map((line, i) => (
 <Line key={i} kind="context" side="right" text={line} />
 ))}
 </pre>
 </div>
 </div>
 )
 }

 const segments = lcsDiff(currentLines, proposedLines)
 const { left, right, adds, removes } = partition(segments)

 const currentLabel =
 parentVersionSeq !== null ? `Current · v${parentVersionSeq}` : 'Current'
 const proposedLabel =
 parentVersionSeq !== null
 ? `Proposed · v${parentVersionSeq + 1}`
 : 'Proposed'

 return (
 <div className="side-by-side">
 <div className="side">
 <div className="side-header">
 <span>{currentLabel}</span>
 <span className="pill outline" style={{ fontSize: 9.5 }}>
 Active · −{removes}
 </span>
 </div>
 <pre className="side-body code" style={codeBoxStyle}>
 {left.map((p, i) => (
 <Line key={i} kind={p.kind} side="left" text={p.text} />
 ))}
 </pre>
 </div>
 <div className="side">
 <div className="side-header">
 <span>{proposedLabel}</span>
 <span className="pill accent" style={{ fontSize: 9.5 }}>
 +{adds} · −{removes}
 </span>
 </div>
 <pre className="side-body code" style={codeBoxStyle}>
 {right.map((p, i) => (
 <Line key={i} kind={p.kind} side="right" text={p.text} />
 ))}
 </pre>
 </div>
 </div>
 )
}

function Line({
 kind,
 side,
 text,
}: {
 kind: 'context' | 'diff'
 side: 'left' | 'right'
 text: string
}) {
 if (kind === 'context') {
 return <span className="ctx">{text || ' '}</span>
 }
 // `diff-add` / `diff-del` are styled in primitives.css and already
 // include the `+ ` / `− ` glyph via ::before, so don't prepend it.
 // `.diff-line` (globals.css) makes each one a full-row block.
 return (
 <span
 className={side === 'left' ? 'diff-line diff-del' : 'diff-line diff-add'}
 >
 {text || ' '}
 </span>
 )
}

const codeBoxStyle: React.CSSProperties = {
 padding: '16px 18px',
 fontSize: 12.5,
 lineHeight: 1.65,
 fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace",
 margin: 0,
 whiteSpace: 'pre-wrap',
 overflowWrap: 'anywhere',
}
