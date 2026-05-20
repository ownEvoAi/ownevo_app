// Data shapes consumed by each primitive renderer.
//
// These mirror the input-shape contracts documented in
// www/preview/s26-rk7p3/27-primitives.html § Input shape, and are the
// runtime payload Track 0 layer C's mock resolver (or a future
// agent-output resolver — Phase-2 TODO-35) produces for each
// WorkflowSpec.ui.tabs[].primitives[] entry.
//
// The Pydantic primitive (MetricCards, TimeSeriesChart, etc.) describes
// WHICH primitive renders and HOW it's parameterised (column names,
// source keys, etc.). The runtime data here is the rendered content.

export interface MetricCardDatum {
  label: string
  value: number | string
  unit?: string
  delta?: {
    value: number | string
    direction: 'up' | 'down' | 'flat'
    scope: string
  }
}

export interface TimeSeriesPoint {
  t: string
  value: number
}

export interface TimeSeriesSeries {
  name: string
  points: TimeSeriesPoint[]
}

export interface TimeSeriesAnnotation {
  t: string
  label: string
}

// Click-through footer the Operate-context resolver attaches so a
// domain expert can jump from any single-source primitive into the
// underlying agent trace. Optional everywhere — Overview primitives
// don't set it.
export interface PrimitiveCaseCaption {
  text: string
  href: string
}

export interface TimeSeriesData {
  title?: string
  subtitle?: string
  series: TimeSeriesSeries[]
  baseline?: number
  baseline_label?: string
  annotations?: TimeSeriesAnnotation[]
  y_format?: 'percent' | 'number' | 'currency'
  caption?: PrimitiveCaseCaption
}

export interface TableColumn {
  key: string
  label: string
  type?: 'string' | 'number' | 'pill'
  format?: 'currency' | 'percent' | 'integer'
  align?: 'left' | 'right'
  // When set, the cell's `title` attribute (hover tooltip) reads from
  // `row[title_key]` — used to show truncated text in the cell while
  // keeping the full value one hover away. Truncation happens in the
  // resolver; the component only wires the tooltip.
  title_key?: string
  // When set, the cell renders as a link with `href = row[link_key]`.
  // Falsy values (null / empty string / missing key) fall back to a
  // plain text cell — no broken/dead links rendered.
  link_key?: string
}

export type TableRow = Record<string, unknown>

export interface TableData {
  title?: string
  summary?: string
  columns: TableColumn[]
  rows: TableRow[]
}

export type AlertSeverity = 'high' | 'medium' | 'low'

export interface AlertItem {
  severity: AlertSeverity
  title: string
  meta: string
  action_url?: string
}

export interface KanbanColumnDef {
  key: string
  label: string
  count: number
}

export interface KanbanCardDef {
  id: string
  column_key: string
  title: string
  body: string
  meta: string
  tags?: Array<{ label: string; tone?: 'amber' | 'green' | 'red' | 'outline' }>
  // When set, the whole card wraps in a link to the per-case trace.
  href?: string
}

export interface KanbanData {
  columns: KanbanColumnDef[]
  cards: KanbanCardDef[]
}

export interface ScheduleRowDef {
  key: string
  label: string
  sub?: string
}

export interface ScheduleColDef {
  key: string
  label: string
  sub?: string
}

export type ScheduleCellStatus = 'ok' | 'warn' | 'error'

export interface ScheduleCellDef {
  row_key: string
  col_key: string
  value: number | string
  target?: number | string
  status: ScheduleCellStatus
  tag?: string
}

export interface ScheduleData {
  rows: ScheduleRowDef[]
  cols: ScheduleColDef[]
  cells: ScheduleCellDef[]
  caption?: PrimitiveCaseCaption
}

export interface ConvoCitation {
  id: number | string
  source: string
}

export type ConvoRole = 'agent' | 'user' | 'system'

export interface ConvoMessage {
  role: ConvoRole
  text: string
  ts?: string
  author?: string
  citations?: ConvoCitation[]
}

export interface ConversationData {
  messages: ConvoMessage[]
  caption?: PrimitiveCaseCaption
}

export type SidePanelHighlight = 'added' | 'removed' | 'unchanged'

export interface SidePanel {
  title: string
  body: string
  format?: 'text' | 'code'
  // Span-based highlight ranges so the diff hint renders inline. The
  // empty array means no inline highlighting.
  highlights?: Array<{ start: number; end: number; kind: SidePanelHighlight }>
}

export interface SideBySideData {
  left: SidePanel
  right: SidePanel
  caption?: PrimitiveCaseCaption
}

export interface DocSpan {
  start: number
  end: number
  kind: 'flagged' | 'standard'
  note?: string
}

export interface DocBlock {
  kind: 'heading' | 'para' | 'clause'
  text: string
  spans?: DocSpan[]
}

export interface DocAnnotation {
  id: string
  span_id?: string
  severity: 'high' | 'medium' | 'low'
  title: string
  body: string
}

export interface DocumentData {
  section_label?: string
  blocks: DocBlock[]
  annotations: DocAnnotation[]
  caption?: PrimitiveCaseCaption
}
