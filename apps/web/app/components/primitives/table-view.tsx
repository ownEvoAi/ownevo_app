import type { TableColumn, TableData } from './types'

interface Props {
  data: TableData
}

const PILL_TONES: Record<string, string> = {
  high: 'pill red',
  med: 'pill amber',
  medium: 'pill amber',
  low: 'pill outline',
  ok: 'pill green',
}

function formatCell(value: unknown, col: TableColumn): React.ReactNode {
  if (value === null || value === undefined) return ''
  if (col.type === 'pill') {
    const v = String(value)
    return <span className={PILL_TONES[v.toLowerCase()] ?? 'pill outline'}>{v}</span>
  }
  if (col.type === 'number' && typeof value === 'number') {
    if (col.format === 'currency') return `$${value.toLocaleString()}`
    if (col.format === 'percent') return `${value.toFixed(1)}%`
    if (col.format === 'integer') return value.toLocaleString()
    return value.toLocaleString()
  }
  return String(value)
}

export function TableView({ data }: Props) {
  return (
    <div className="table-wrap">
      {(data.title || data.summary) && (
        <div className="table-toolbar">
          <span>{data.title ?? ''}</span>
          <span>{data.summary ?? ''}</span>
        </div>
      )}
      <table className="table">
        <thead>
          <tr>
            {data.columns.map((c) => (
              <th
                key={c.key}
                className={c.type === 'number' || c.align === 'right' ? 'num' : undefined}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, rIdx) => (
            <tr key={rIdx}>
              {data.columns.map((c, cIdx) => {
                const v = row[c.key]
                const numeric =
                  c.type === 'number' || c.align === 'right' || typeof v === 'number'
                return (
                  <td
                    key={c.key}
                    className={[
                      numeric ? 'num' : '',
                      cIdx === 0 ? 'strong' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  >
                    {formatCell(v, c)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
