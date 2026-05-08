import type { IterationPoint } from '../../../lib/api'

interface LiftChartProps {
  /** All iterations for the workflow, ordered by iteration_index ASC. */
  points: IterationPoint[]
  /** Workflow id, used as the chart's accessible label. */
  workflowId: string
  /** Optional: pixel width override. Defaults responsive 100%. */
  height?: number
}

// SVG line chart for the workspace Health page hero. Plots
// `iteration_index` × `val_score` for every iteration, plus a
// monotone `best_ever_score_after` line on top, and overlays an
// annotated dot wherever `has_approved_proposal` is true.
//
// Data shape per W7_SLICE.md resolved decisions: iteration-keyed,
// not day-keyed. No external chart lib — pure SVG keeps the bundle
// shape small and the demo screenshot static-renderable.
export function LiftChart({ points, workflowId, height = 220 }: LiftChartProps) {
  const valid = points.filter(
    (p): p is IterationPoint & { val_score: number } => p.val_score !== null,
  )

  if (valid.length === 0) {
    return (
      <div className="lift-chart lift-chart-empty" aria-label={`No iterations yet for ${workflowId}`}>
        <p className="lift-chart-empty-msg">
          No iterations yet. Run <code>scripts/run_improvement_loop.py</code> against
          this workflow to start the lift curve.
        </p>
      </div>
    )
  }

  // Pad axes so dots aren't clipped at the edges.
  const xs = valid.map((p) => p.iteration_index)
  const ys = valid.flatMap((p) => [
    p.val_score,
    p.best_ever_score_after ?? p.val_score,
  ])
  const xMin = Math.min(...xs)
  const xMax = Math.max(xs.length === 1 ? xMin + 1 : Math.max(...xs))
  const yMinRaw = Math.min(...ys)
  const yMaxRaw = Math.max(...ys)
  const yPad = Math.max((yMaxRaw - yMinRaw) * 0.12, 0.02)
  const yMin = yMinRaw - yPad
  const yMax = yMaxRaw + yPad

  const padding = { top: 20, right: 24, bottom: 32, left: 56 }
  const innerWidth = 720 // viewBox width; SVG scales to container
  const width = innerWidth
  const totalWidth = width
  const totalHeight = height

  const xScale = (x: number) =>
    padding.left + ((x - xMin) / Math.max(xMax - xMin, 1)) * (width - padding.left - padding.right)
  const yScale = (y: number) =>
    padding.top + (1 - (y - yMin) / Math.max(yMax - yMin, 1e-6)) * (height - padding.top - padding.bottom)

  // val_score line — connects every iteration's measured score.
  const valPath = valid
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${xScale(p.iteration_index).toFixed(1)},${yScale(p.val_score).toFixed(1)}`)
    .join(' ')

  // best_ever_score_after — monotone non-decreasing; falls back to val_score if null.
  const bestPath = valid
    .map((p, i) => {
      const y = p.best_ever_score_after ?? p.val_score
      return `${i === 0 ? 'M' : 'L'}${xScale(p.iteration_index).toFixed(1)},${yScale(y).toFixed(1)}`
    })
    .join(' ')

  // Y-axis ticks: 4 evenly spaced.
  const yTicks = Array.from({ length: 5 }, (_, i) => yMin + (i / 4) * (yMax - yMin))
  // X-axis ticks: every iteration when count <= 8, else 5 evenly spaced.
  const xTickValues =
    xs.length <= 8
      ? Array.from(new Set(xs)).sort((a, b) => a - b)
      : Array.from({ length: 5 }, (_, i) => Math.round(xMin + (i / 4) * (xMax - xMin)))

  const annotated = valid.filter((p) => p.has_approved_proposal)

  return (
    <div className="lift-chart">
      <svg
        viewBox={`0 0 ${totalWidth} ${totalHeight}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Lift chart for ${workflowId}: val_score across ${valid.length} iterations`}
        style={{ width: '100%', height: totalHeight, display: 'block' }}
      >
        {/* Y gridlines */}
        {yTicks.map((t, i) => (
          <line
            key={`yg-${i}`}
            x1={padding.left}
            x2={totalWidth - padding.right}
            y1={yScale(t)}
            y2={yScale(t)}
            stroke="var(--border)"
            strokeWidth={1}
            strokeDasharray={i === 0 ? undefined : '2 3'}
          />
        ))}

        {/* Y-axis labels */}
        {yTicks.map((t, i) => (
          <text
            key={`yt-${i}`}
            x={padding.left - 8}
            y={yScale(t) + 4}
            textAnchor="end"
            fontFamily="var(--mono)"
            fontSize={10.5}
            fill="var(--text-muted)"
          >
            {t.toFixed(2)}
          </text>
        ))}

        {/* X-axis labels */}
        {xTickValues.map((t, i) => (
          <text
            key={`xt-${i}`}
            x={xScale(t)}
            y={totalHeight - padding.bottom + 16}
            textAnchor="middle"
            fontFamily="var(--mono)"
            fontSize={10.5}
            fill="var(--text-muted)"
          >
            {t}
          </text>
        ))}

        {/* val_score line — thinner, secondary */}
        <path
          d={valPath}
          fill="none"
          stroke="var(--text-faint)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* best_ever line — accent, hero */}
        <path
          d={bestPath}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={2.25}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Per-iteration dots (val_score) */}
        {valid.map((p) => (
          <circle
            key={`pt-${p.iteration_index}`}
            cx={xScale(p.iteration_index)}
            cy={yScale(p.val_score)}
            r={2.5}
            fill="var(--text-faint)"
          />
        ))}

        {/* Annotated dots — approved proposals */}
        {annotated.map((p) => (
          <g key={`ap-${p.iteration_index}`}>
            <circle
              cx={xScale(p.iteration_index)}
              cy={yScale(p.best_ever_score_after ?? p.val_score)}
              r={6}
              fill="var(--accent)"
              stroke="var(--bg)"
              strokeWidth={2}
            />
          </g>
        ))}
      </svg>

      <div className="lift-chart-legend">
        <span className="legend-row">
          <span className="legend-swatch swatch-best" /> Best-ever val_score
        </span>
        <span className="legend-row">
          <span className="legend-swatch swatch-val" /> Per-iteration val_score
        </span>
        {annotated.length > 0 && (
          <span className="legend-row">
            <span className="legend-swatch swatch-approved" /> Approved improvement
          </span>
        )}
      </div>
    </div>
  )
}
