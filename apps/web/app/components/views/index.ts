// Workflow render views — leaf components keyed by the Pydantic
// view type discriminator. See packages/trace-format/.../ui_views.py
// for the typed contracts that decide which view renders, and
// § Input shape for the
// runtime data shapes each one consumes.

export { MetricCards } from './metric-cards'
export { TimeSeriesChart } from './time-series-chart'
export { TableView } from './table-view'
export { AlertList } from './alert-list'
export { KanbanBoard } from './kanban-board'
export { ScheduleGrid } from './schedule-grid'
export { ConversationView } from './conversation-view'
export { SideBySideView } from './side-by-side-view'
export { DocumentReader } from './document-reader'

export type {
 AlertItem,
 AlertSeverity,
 ConvoCitation,
 ConvoMessage,
 ConvoRole,
 ConversationData,
 DocAnnotation,
 DocBlock,
 DocSpan,
 DocumentData,
 KanbanCardDef,
 KanbanColumnDef,
 KanbanData,
 MetricCardDatum,
 ScheduleCellDef,
 ScheduleCellStatus,
 ScheduleColDef,
 ScheduleData,
 ScheduleRowDef,
 SideBySideData,
 SidePanel,
 SidePanelHighlight,
 TableColumn,
 TableData,
 TableRow,
 TimeSeriesAnnotation,
 TimeSeriesData,
 TimeSeriesPoint,
 TimeSeriesSeries,
} from './types'
