import type { MetricCardDatum } from './types'

interface Props {
  data: MetricCardDatum[]
}

const ARROW: Record<'up' | 'down' | 'flat', string> = {
  up: '↑',
  down: '↓',
  flat: '·',
}

export function MetricCards({ data }: Props) {
  return (
    <div className="metrics">
      {data.map((m, i) => (
        <div className="metric" key={`${m.label}-${i}`}>
          <div className="metric-label">{m.label}</div>
          <div className="metric-value">
            {m.value}
            {m.unit ? <span className="metric-unit">{m.unit}</span> : null}
          </div>
          {m.delta ? (
            <div className={`metric-delta ${m.delta.direction}`}>
              {ARROW[m.delta.direction]} {m.delta.value}
              {m.delta.scope ? ` ${m.delta.scope}` : ''}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}
