import type { TimeSeriesData } from './types'

interface Props {
  data: TimeSeriesData
}

const W = 720
const H = 220
const PAD_L = 40
const PAD_R = 20
const PAD_T = 20
const PAD_B = 30

const SERIES_STROKE = ['var(--accent)', 'var(--green)', 'var(--amber)']

function formatTick(v: number, fmt: TimeSeriesData['y_format']): string {
  if (fmt === 'percent') return `${v.toFixed(0)}%`
  if (fmt === 'currency') return `$${v.toLocaleString()}`
  return v.toLocaleString()
}

export function TimeSeriesChart({ data }: Props) {
  const allPoints = data.series.flatMap((s) => s.points)

  if (!data.series.length || !allPoints.length) {
    return (
      <div className="chart">
        <div className="chart-header">
          <div className="chart-title">{data.title ?? 'Time series'}</div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', padding: '12px 0' }}>
          No series data yet.
        </p>
      </div>
    )
  }

  const values = allPoints.map((p) => p.value)
  if (data.baseline !== undefined) values.push(data.baseline)
  const minRaw = Math.min(...values)
  const maxRaw = Math.max(...values)
  const span = Math.max(maxRaw - minRaw, 1)
  // Pad the y-range so markers don't kiss the chart edges.
  const min = minRaw - span * 0.08
  const max = maxRaw + span * 0.08
  const range = max - min || 1

  // Use the longest series for x-axis length; assume aligned ticks.
  const longest = data.series.reduce((a, b) =>
    a.points.length >= b.points.length ? a : b,
  )
  const n = longest.points.length
  const denom = Math.max(n - 1, 1)

  const xAt = (i: number) => PAD_L + (i / denom) * (W - PAD_L - PAD_R)
  const yAt = (v: number) => PAD_T + (1 - (v - min) / range) * (H - PAD_T - PAD_B)

  const gridLines = 4
  const ticks = Array.from({ length: gridLines + 1 }, (_, i) => {
    const v = min + (range * i) / gridLines
    return { v, y: yAt(v) }
  })

  const xTickStride = Math.max(1, Math.floor(n / 6))
  const xTicks = longest.points
    .map((p, i) => ({ i, label: p.t }))
    .filter(({ i }) => i % xTickStride === 0 || i === n - 1)

  const baselineY = data.baseline !== undefined ? yAt(data.baseline) : null

  return (
    <div className="chart">
      <div className="chart-header">
        <div>
          <div className="chart-title">{data.title ?? 'Time series'}</div>
          {data.subtitle ? (
            <div className="chart-subtitle">{data.subtitle}</div>
          ) : null}
        </div>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="chart-svg"
        style={{ height: H }}
        aria-label={data.title ?? 'Time series chart'}
        role="img"
      >
        <g className="chart-grid">
          {ticks.map(({ y }, i) => (
            <line key={i} x1={PAD_L} y1={y} x2={W - PAD_R} y2={y} />
          ))}
        </g>
        <g className="chart-axis">
          {ticks.map(({ v, y }, i) => (
            <text key={i} x={PAD_L - 8} y={y + 4} textAnchor="end">
              {formatTick(v, data.y_format)}
            </text>
          ))}
          {xTicks.map(({ i, label }) => (
            <text key={i} x={xAt(i)} y={H - 10} textAnchor="middle">
              {label}
            </text>
          ))}
        </g>
        {baselineY !== null ? (
          <g>
            <line
              x1={PAD_L}
              y1={baselineY}
              x2={W - PAD_R}
              y2={baselineY}
              className="chart-annotation-line"
            />
            {data.baseline_label ? (
              <text
                x={PAD_L + 6}
                y={baselineY - 4}
                className="chart-annotation-label"
              >
                {data.baseline_label}
              </text>
            ) : null}
          </g>
        ) : null}
        {/* Area fill behind the first series only — keeps the chart legible. */}
        {(() => {
          const s = data.series[0]
          if (!s) return null
          const path = s.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xAt(i)},${yAt(p.value)}`)
            .join(' ')
          const area = `${path} L ${xAt(s.points.length - 1)},${H - PAD_B} L ${xAt(0)},${H - PAD_B} Z`
          return <path d={area} className="chart-area" />
        })()}
        {data.series.map((s, sIdx) => {
          const path = s.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xAt(i)},${yAt(p.value)}`)
            .join(' ')
          return (
            <g key={s.name}>
              <path
                d={path}
                className="chart-line"
                stroke={SERIES_STROKE[sIdx] ?? 'var(--accent)'}
              />
              {s.points.map((p, i) => (
                <circle
                  key={i}
                  cx={xAt(i)}
                  cy={yAt(p.value)}
                  r={i === s.points.length - 1 ? 4 : 3}
                  className="chart-marker"
                  fill={SERIES_STROKE[sIdx] ?? 'var(--accent)'}
                >
                  <title>
                    {s.name}: {p.value} @ {p.t}
                  </title>
                </circle>
              ))}
            </g>
          )
        })}
      </svg>
      <div className="chart-legend">
        {data.series.map((s, sIdx) => (
          <span className="chart-legend-item" key={s.name}>
            <span
              className="chart-legend-dot"
              style={{ background: SERIES_STROKE[sIdx] ?? 'var(--accent)' }}
            />
            {s.name}
          </span>
        ))}
        {data.baseline !== undefined ? (
          <span className="chart-legend-item">
            <span
              className="chart-legend-dot"
              style={{ background: 'var(--text-faint)' }}
            />
            Baseline
          </span>
        ) : null}
      </div>
    </div>
  )
}
