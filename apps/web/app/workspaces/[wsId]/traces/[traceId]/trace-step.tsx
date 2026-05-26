import type { AgentEvent } from '@/lib/api'

// (7.1.9) — one row in the trace timeline.
//
// Visual target: § .timeline-row.
// Each row shows: time-since-start · event-type chip · headline +
// expandable input/output payload. Native <details> handles the
// expand/collapse interaction so the page stays a server component
// (zero client JS).

interface Props {
 event: AgentEvent
 startedAtMs: number
}

export function TraceStep({ event, startedAtMs }: Props) {
 const ts = new Date(event.timestamp).getTime()
 const offsetMs = Number.isFinite(ts) ? ts - startedAtMs : 0
 const offsetLabel =
 offsetMs >= 1000 ? `+${(offsetMs / 1000).toFixed(2)}s` : `+${offsetMs}ms`

 const view = renderEvent(event)

 return (
 <div className="timeline-row">
 <div className="timeline-time">{offsetLabel}</div>
 <div className="timeline-type">
 <span className={`event-chip ${chipClass(event)}`}>{event.type}</span>
 </div>
 <div className="timeline-body">
 <div className="timeline-headline">{view.headline}</div>
 {view.subline && <div className="timeline-subline">{view.subline}</div>}
 {view.payload && (
 <details className="timeline-payload">
 <summary>show payload</summary>
 <pre className="timeline-payload-pre">{view.payload}</pre>
 </details>
 )}
 </div>
 </div>
 )
}

interface View {
 headline: string
 subline: string | null
 payload: string | null
}

function renderEvent(event: AgentEvent): View {
 switch (event.type) {
 case 'skill_loaded':
 return {
 headline: `Loaded skill ${event.skill_id} v${event.version_seq}`,
 subline: event.retention_acknowledged
 ? 'retention contract acknowledged'
 : 'retention contract NOT acknowledged',
 payload: null,
 }
 case 'reasoning_delta':
 return {
 headline: 'Reasoning',
 subline: `model: ${event.model}`,
 payload: event.text,
 }
 case 'content_delta':
 return {
 headline: 'Output',
 subline: `model: ${event.model}`,
 payload: event.cumulative_text ?? event.text,
 }
 case 'tool_call_start':
 return {
 headline: `Tool call · ${event.name}`,
 subline: `call_id ${event.call_id}`,
 payload: jsonOrNull(event.args),
 }
 case 'tool_call_result': {
 const status =
 event.status === 'ok'
 ? 'ok'
 : event.error_class
 ? `error (${event.error_class})`
 : 'error'
 const sub = `${status} · ${event.duration_ms}ms · call_id ${event.call_id}`
 const payload =
 event.status === 'error'
 ? `${event.error ?? '(no error message)'}\n\n${jsonOrNull(event.output) ?? ''}`
 : jsonOrNull(event.output)
 return {
 headline: `Tool result · ${event.name}`,
 subline: sub,
 payload,
 }
 }
 case 'citation':
 return {
 headline: `Citation [${event.ref}] ${event.source}`,
 subline: null,
 payload: event.quote,
 }
 case 'monitor_signal':
 return {
 headline: `Monitor · ${event.monitor}`,
 subline: `severity: ${event.severity}`,
 payload: jsonOrNull(event.details ?? null),
 }
 default: {
 // Exhaustiveness — unknown variant means SPEC bumped without UI
 // catching up. Render the raw JSON so the operator sees it.
 const _exhaustive: never = event
 return {
 headline: 'Unknown event',
 subline: null,
 payload: JSON.stringify(_exhaustive, null, 2),
 }
 }
 }
}

function chipClass(event: AgentEvent): string {
 if (event.type === 'tool_call_result' && event.status === 'error') {
 return `${event.type} error`
 }
 return event.type
}

function jsonOrNull(value: unknown): string | null {
 if (value === null || value === undefined) return null
 try {
 return JSON.stringify(value, null, 2)
 } catch {
 return String(value)
 }
}
