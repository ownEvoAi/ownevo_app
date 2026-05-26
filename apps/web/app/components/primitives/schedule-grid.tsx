import type { ScheduleData } from './types'
import { CaseCaption } from './case-caption'

interface Props {
 data: ScheduleData
}

const STATUS_TONE: Record<string, { bg: string; pillBg: string; pillFg: string }> = {
 ok: {
 bg: 'var(--bg)',
 pillBg: 'var(--green-soft)',
 pillFg: 'var(--green)',
 },
 warn: {
 bg: 'var(--amber-soft)',
 pillBg: 'var(--amber-soft)',
 pillFg: 'var(--amber)',
 },
 error: {
 bg: 'var(--red-soft)',
 pillBg: 'var(--red-soft)',
 pillFg: 'var(--red)',
 },
}

export function ScheduleGrid({ data }: Props) {
 const cellMap = new Map(
 data.cells.map((c) => [`${c.row_key}::${c.col_key}`, c]),
 )

 const cols = data.cols.length
 const gridTemplate = `110px repeat(${cols}, 1fr)`

 return (
 <div
 style={{
 background: 'var(--bg)',
 border: '1px solid var(--border)',
 borderRadius: 8,
 boxShadow: 'var(--shadow-sm)',
 overflow: 'hidden',
 }}
 >
 <div
 style={{
 display: 'grid',
 gridTemplateColumns: gridTemplate,
 gap: 1,
 background: 'var(--border)',
 }}
 >
 <div
 style={{
 background: 'var(--surface)',
 padding: '8px 10px',
 fontSize: 10.5,
 fontWeight: 600,
 color: 'var(--text-2)',
 textTransform: 'uppercase',
 letterSpacing: '0.06em',
 }}
 >
 {/* corner cell */}
 </div>
 {data.cols.map((c) => (
 <div
 key={c.key}
 style={{
 background: 'var(--surface)',
 padding: '8px 10px',
 fontSize: 10.5,
 fontWeight: 600,
 color: 'var(--text-2)',
 textTransform: 'uppercase',
 letterSpacing: '0.06em',
 }}
 >
 {c.label}
 {c.sub ? (
 <div
 style={{
 fontSize: 9.5,
 color: 'var(--text-muted)',
 marginTop: 1,
 fontWeight: 400,
 }}
 >
 {c.sub}
 </div>
 ) : null}
 </div>
 ))}
 {data.rows.map((row) => (
 <RowSlice
 key={row.key}
 row={row}
 cols={data.cols}
 cellMap={cellMap}
 />
 ))}
 </div>
 <div style={{ padding: '6px 12px' }}>
 <CaseCaption caption={data.caption} />
 </div>
 </div>
 )
}

function RowSlice({
 row,
 cols,
 cellMap,
}: {
 row: ScheduleData['rows'][number]
 cols: ScheduleData['cols']
 cellMap: Map<string, ScheduleData['cells'][number]>
}) {
 return (
 <>
 <div
 style={{
 background: 'var(--bg)',
 padding: 10,
 minHeight: 56,
 display: 'flex',
 alignItems: 'center',
 fontSize: 11.5,
 color: 'var(--text-2)',
 fontWeight: 500,
 }}
 >
 {row.label}
 {row.sub ? (
 <span
 style={{
 color: 'var(--text-muted)',
 fontWeight: 400,
 fontSize: 10.5,
 marginLeft: 8,
 }}
 >
 {row.sub}
 </span>
 ) : null}
 </div>
 {cols.map((col) => {
 const cell = cellMap.get(`${row.key}::${col.key}`)
 if (!cell) {
 return (
 <div
 key={col.key}
 style={{ background: 'var(--bg)', minHeight: 56 }}
 />
 )
 }
 const tone = STATUS_TONE[cell.status] ?? STATUS_TONE['ok']
 return (
 <div
 key={col.key}
 style={{
 background: tone.bg,
 padding: '8px 10px',
 minHeight: 56,
 display: 'flex',
 flexDirection: 'column',
 gap: 3,
 }}
 >
 <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500 }}>
 {cell.value}
 </div>
 {cell.target !== undefined ? (
 <div style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
 target {cell.target}
 </div>
 ) : null}
 {cell.tag ? (
 <span
 style={{
 fontSize: 9.5,
 padding: '1px 5px',
 borderRadius: 3,
 alignSelf: 'flex-start',
 marginTop: 'auto',
 background: tone.pillBg,
 color: tone.pillFg,
 }}
 >
 {cell.tag}
 </span>
 ) : null}
 </div>
 )
 })}
 </>
 )
}
