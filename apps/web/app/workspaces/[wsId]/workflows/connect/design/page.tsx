import Link from 'next/link'
import { EntryStrip } from '../../new/page'
import { ConnectSteps } from '../page'
import {
 exportCopilotStudioDefinition,
 fetchImportSummary,
 listAllTraces,
 type TraceSummary,
 type WorkflowOrigin,
} from '@/lib/api-server'
import { ImportDesignFlow } from './import-design-flow'

interface PageProps {
 params: Promise<{ wsId: string }>
 searchParams: Promise<{ source?: string; trace_ids?: string; solution?: string }>
}

// Map the connect source key onto a workflow origin tag. Only the
// adapter-mediated vendors map; OTel / upload / manual stay greenfield
// (null) since their provenance isn't a single fix-delivery vendor.
function originForSource(source: string | undefined): WorkflowOrigin | null {
 if (source === 'langsmith') return 'langsmith'
 if (source === 'copilot-studio') return 'copilot_studio'
 return null
}

// Connect existing agent — step 2/3, trace-import discovery.
//
// The improvement loop attaches to an agent that is already running: its
// traces were ingested through the OTLP receiver. This page summarises
// the imported traces, runs the design agent's discovery interview over
// that observed behaviour, and — on Generate — reverse-engineers a
// WorkflowSpec + sim plan + metric and persists the workflow.
//
// "Ingested" traces are the production traces (iteration_id IS NULL) the
// kernel stores from the OTLP receiver. When the source picker passes an
// explicit `trace_ids` list we honour it; otherwise we summarise every
// ingested trace the workspace has seen so far.
const _MAX_SELECTED_TRACES = 50
const _SSR_SUMMARY_TIMEOUT_MS = 5000

function buildSummaryLines(traces: TraceSummary[]): string[] {
 const kindTotals: Record<string, number> = {}
 let totalEvents = 0
 for (const t of traces) {
 totalEvents += t.event_count
 for (const [kind, n] of Object.entries(t.kind_counts ?? {})) {
 kindTotals[kind] = (kindTotals[kind] ?? 0) + n
 }
 }
 const lines: string[] = [
 `${traces.length} ingested trace${traces.length === 1 ? '' : 's'}, ${totalEvents} events.`,
 '',
 'Event breakdown:',
 ]
 const ordered = Object.entries(kindTotals).sort((a, b) => b[1] - a[1])
 if (ordered.length === 0) {
 lines.push(' (no decodable events)')
 } else {
 for (const [kind, n] of ordered) {
 lines.push(` ${kind}: ${n}`)
 }
 }
 return lines
}

export default async function ConnectDesignPage({
 params,
 searchParams,
}: PageProps) {
 const { wsId } = await params
 const { source, trace_ids: traceIdsParam, solution } = await searchParams
 const origin = originForSource(source)

 let allTraces: TraceSummary[] = []
 let loadError: string | null = null
 try {
 const resp = await listAllTraces allTraces = resp.items
 } catch (err) {
 loadError = err instanceof Error ? err.message : String(err)
 }

 // Production / ingested traces = no iteration_id. Eval-loop traces are
 // excluded; they belong to a workflow that already exists.
 const ingested = allTraces.filter((t) => t.iteration_id === null)
 const requestedIds = traceIdsParam
 ? new Set(traceIdsParam.split(',').map((s) => s.trim ).filter(Boolean))
 : null
 const selected = (requestedIds
 ? ingested.filter((t) => requestedIds.has(t.id))
 : ingested
 ).slice(0, _MAX_SELECTED_TRACES)

 const traceIds = selected.map((t) => t.id)

 const header = (
 <header className="gen-head">
 <a
 href={`/workspaces/${wsId}/workflows/connect`}
 className="wf-back"
 style={{ marginBottom: 6 }}
 >
 ‹ Back: change source
 </a>
 <h1 className="gen-title">Define the imported agent</h1>
 <p className="gen-sub">
 ownEvo read this agent&rsquo;s traces. Answer a short discovery
 interview so the improvement loop knows what success means before it
 attaches.
 </p>
 </header>
 )

 if (loadError) {
 return (
 <div className="preview-wrap">
 {header}
 <EntryStrip wsId={wsId} active="connect" />
 <ConnectSteps step={2} />
 <div role="alert" className="api-banner">
 <strong>Could not load traces.</strong> {loadError}
 </div>
 </div>
 )
 }

 if (traceIds.length === 0) {
 return (
 <div className="preview-wrap">
 {header}
 <EntryStrip wsId={wsId} active="connect" />
 <ConnectSteps step={2} />
 <div className="connect-not-wired">
 <div className="connect-not-wired-pill">No traces yet</div>
 <h2 className="connect-not-wired-title">
 No ingested traces found for this agent
 </h2>
 <p className="connect-not-wired-body">
 Point your OpenTelemetry collector at the kernel&rsquo;s OTLP
 endpoint (gen-ai semantic conventions), or upload a trace export.
 Once at least one trace lands, this page runs discovery over the
 agent&rsquo;s observed behaviour.
 </p>
 <div className="connect-not-wired-actions">
 <Link
 href={`/workspaces/${wsId}/workflows/connect`}
 className="btn btn-secondary"
 >
 Pick a different source
 </Link>
 </div>
 </div>
 </div>
 )
 }

 // When the source is a Copilot Studio agent and the operator named the
 // solution, export its definition so reverse-discovery is grounded in the
 // agent's stated instructions, not only its traces. Best-effort: any
 // failure (no credential, export error, no recognisable definition) falls
 // back to the trace-only summary rather than blocking the flow.
 let agentDefinition: string | null = null
 if (origin === 'copilot_studio' && solution) {
 try {
 const def = await exportCopilotStudioDefinition(solution)
 agentDefinition = def.agent_definition
 } catch {
 agentDefinition = null
 }
 }

 // Pre-fetch the reverse-discovery summary under a short budget so the
 // opening "this agent does X" turn renders fast; the client retries
 // without a cap if the LLM is slow. The first discovery question is
 // fetched client-side after the reviewer confirms the summary, since it
 // depends on the confirmed agent definition.
 const controller = new AbortController const timer = setTimeout( => controller.abort , _SSR_SUMMARY_TIMEOUT_MS)
 let initialSummary
 try {
 const resp = await fetchImportSummary(traceIds, agentDefinition, controller.signal)
 initialSummary = {
 loaded: true,
 summary: resp.summary,
 basis: resp.basis,
 source: resp.source,
 error: null,
 }
 } catch {
 // SSR budget exceeded or transient failure — the client refetches.
 initialSummary = {
 loaded: false,
 summary: null,
 basis: null,
 source: null,
 error: null,
 }
 } finally {
 clearTimeout(timer)
 }

 return (
 <div className="preview-wrap">
 {header}
 <EntryStrip wsId={wsId} active="connect" />
 <ConnectSteps step={2} />
 <ImportDesignFlow
 wsId={wsId}
 traceIds={traceIds}
 agentDefinition={agentDefinition}
 origin={origin}
 traceSummaryLines={buildSummaryLines(selected)}
 traceCount={selected.length}
 initialSummary={initialSummary}
 />
 </div>
 )
}
