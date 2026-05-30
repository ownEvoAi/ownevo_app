import Link from 'next/link'
import {
 getWorkflowAnatomy,
 getWorkflowCaseOutputs,
 getWorkflowIterations,
 kernelError,
 KernelApiError,
 listProposals,
 listWorkflowEvalCases,
 type CaseOutputList,
 type EvalCaseSummary,
 type IterationPoint,
 type ProposalSummary,
 type WorkflowSpecShape,
} from '@/lib/api'
import { relativeTime } from '@/lib/format'
import { AlertList } from '@/app/components/views/alert-list'
import { ConversationView } from '@/app/components/views/conversation-view'
import { DocumentReader } from '@/app/components/views/document-reader'
import { KanbanBoard } from '@/app/components/views/kanban-board'
import { MetricCards } from '@/app/components/views/metric-cards'
import { ScheduleGrid } from '@/app/components/views/schedule-grid'
import { SideBySideView } from '@/app/components/views/side-by-side-view'
import { TableView } from '@/app/components/views/table-view'
import { TimeSeriesChart } from '@/app/components/views/time-series-chart'
import { resolveTabViews } from '@/lib/view-data-resolver'

interface PageProps {
 params: Promise<{ wsId: string; wfId: string }>
}

// Operate tab — mock parity with
// (also 09 support, 10 contract, 10b labour). Production-execution
// view: what the agent has produced against live triggers + data
// sources, NOT what it predicted against eval cases. Eval-suite
// diagnostics belong on Overview.
//
// Input side renders from the spec — tools the agent can call,
// declared data sources, env generators that act as scheduled
// triggers. No agent emission needed.
//
// Output side passes `context: 'operate'` to the layer-D resolver so
// iteration-meta and eval-prediction views stay empty here. They
// will be replaced with real production output when a workflow-
// specific production_output payload lands (kernel-side: a
// `submit_production_output` tool + `workflow_production_outputs`
// table). Until then the Operate tab is honest about being a shell.
export default async function WorkflowOperatePage({ params }: PageProps) {
 const { wsId, wfId } = await params

 let spec: WorkflowSpecShape | null = null
 let description: string | null = null
 let iterations: IterationPoint[] = []
 let evalCases: EvalCaseSummary[] = []
 let proposals: ProposalSummary[] = []
 let caseOutputs: CaseOutputList | null = null
 let apiError: { title: string; detail: string } | null = null

 try {
 const [anatomy, iterList, evalList, propList, coList] = await Promise.all([
 getWorkflowAnatomy(wfId),
 getWorkflowIterations(wfId),
 listWorkflowEvalCases(wfId),
 listProposals({ workflow_id: wfId, limit: 100 }),
 getWorkflowCaseOutputs(wfId).catch(() => null),
 ])
 spec = anatomy.spec
 description = anatomy.description
 iterations = iterList.items
 evalCases = evalList.items
 proposals = propList.items
 caseOutputs = coList
 } catch (err) {
 if (err instanceof KernelApiError && err.status === 404) {
 apiError = { title: 'Workflow not registered.', detail: err.detail }
 } else {
 apiError = kernelError(err)
 }
 }

 if (apiError) {
 return (
 <div role="alert" className="api-banner">
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )
 }

 // Find the operate-view tab in the spec UI plan. NL-gen names tabs
 // per workflow (`Portfolio` / `Forecast` / `Review queue` / etc.)
 // rather than literally "Operate", so we look up by name first then
 // fall back gracefully:
 // 1. tab named exactly "operate" (mock-shaped specs)
 // 2. the second tab if the spec has ≥2 (Overview-at-0 + Operate-at-1)
 // 3. the first tab if there's only one (single-tab specs share
 // views between Overview and Operate — the chrome differs,
 // not the view set)
 // 4. nothing when zero tabs are declared
 // Matches the operator route's fallback (`resolveViews` reads
 // tabs[0]), so /operate and /operator/[wf] stay in sync.
 const tabs = spec?.ui?.tabs ?? []
 const operateTab =
 tabs.find((t) => (t.name ?? '').toLowerCase() === 'operate') ??
 tabs[1] ??
 tabs[0]

 const views = operateTab
 ? resolveTabViews(
 {
 spec,
 iterations,
 evalCases,
 proposals,
 caseOutputs,
 wsId,
 context: 'operate',
 },
 operateTab.name ?? 'operate',
 ) ?? []
 : []

 const resolved = views.filter((p) => p.kind !== 'empty')
 const unresolvedTypes = views
 .filter((p): p is Extract<typeof views[number], { kind: 'empty' }> => p.kind === 'empty')
 .map((p) => p.viewType)

 const pendingProposals = proposals.filter(
 (p) => p.state === 'gate-passed' || p.state === 'pending',
 )

 // Inputs the agent reads against in production — pulled straight from
 // the spec. `env_generators` are the scheduled / event triggers the
 // platform would fire (e.g. "weekly SAP refresh", "support ticket
 // arrival"); `data_sources` are the external feeds the agent reads;
 // `tools` are the actions the agent is allowed to take.
 const dataSources = spec?.environment?.data_sources ?? []
 const envGenerators = spec?.environment?.env_generators ?? []
 const tools = spec?.tools ?? []
 const hasInputs =
 dataSources.length > 0 || envGenerators.length > 0 || tools.length > 0

 // "Has the agent produced any domain-shaped output the operator can
 // actually see?" — drives the status banner. Doesn't track live
 // triggers (no live-trigger plumbing yet); reflects only whether the
 // latest iteration produced a payload view the resolver can
 // render.
 const hasProduction =
 (caseOutputs?.items ?? []).some(
 (it) =>
 it.output_payload != null &&
 typeof it.output_payload === 'object' &&
 Object.keys(it.output_payload).length > 0,
 )
 const latestRunAt =
 iterations.length > 0 ? iterations[iterations.length - 1].ended_at : null

 return (
 <>
 <section className="operate-status">
 <div className="operate-status-pill">
 <span
 className={`operate-status-dot ${hasProduction ? 'live' : 'idle'}`}
 />
 <strong>
 {hasProduction
 ? 'Latest output captured'
 : 'Awaiting first production run'}
 </strong>
 <span style={{ color: 'var(--text-muted)' }}>
 {hasProduction
 ? '· from the agent on the latest iteration; live trigger pending'
 : '· live trigger + execution capture not wired yet'}
 </span>
 </div>
 <div className="operate-status-cells">
 <div className="operate-status-cell">
 <div className="operate-status-label">Latest agent run</div>
 <div className="operate-status-value">
 {latestRunAt ? relativeTime(latestRunAt) : '—'}
 </div>
 </div>
 <div className="operate-status-cell">
 <div className="operate-status-label">Pending review</div>
 <div className="operate-status-value">
 {pendingProposals.length > 0 ? (
 <Link
 href={`/workspaces/${wsId}/workflows/${wfId}/proposals`}
 style={{ color: 'var(--accent)' }}
 >
 {pendingProposals.length}
 </Link>
 ) : (
 <span style={{ color: 'var(--text-muted)' }}>0</span>
 )}
 </div>
 </div>
 </div>
 </section>

 {description ? (
 <p
 style={{
 fontSize: 12.5,
 color: 'var(--text-muted)',
 marginBottom: 16,
 lineHeight: 1.5,
 }}
 >
 {description}
 </p>
 ) : null}

 {hasInputs ? (
 <section style={{ marginTop: 8, marginBottom: 16 }}>
 <h2 className="section-title" style={{ marginBottom: 10 }}>
 Inputs
 </h2>
 <div className="operate-inputs">
 {envGenerators.length > 0 ? (
 <InputBlock
 label="Triggers"
 hint="When the platform would invoke this workflow in production."
 items={envGenerators.map((g) => ({
 name: g.name,
 description: g.description,
 }))}
 />
 ) : null}
 {dataSources.length > 0 ? (
 <InputBlock
 label="Data sources"
 hint="External feeds the agent reads at execution time."
 items={dataSources.map((d) => ({
 name: d.id,
 description:
 d.entity != null ? `${d.entity} — ${d.description ?? ''}` : d.description,
 }))}
 />
 ) : null}
 {tools.length > 0 ? (
 <InputBlock
 label="Tools"
 hint="Actions the agent is allowed to take during a run."
 items={tools.map((t) => ({
 name: t.name,
 description: t.description,
 }))}
 />
 ) : null}
 </div>
 </section>
 ) : null}

 <section style={{ marginTop: 8 }}>
 <h2 className="section-title" style={{ marginBottom: 10 }}>
 Outputs
 </h2>
 {resolved.length > 0 ? (
 <div className="overview-views">
 {resolved.map((p, i) => {
 if (p.kind === 'MetricCards') return <MetricCards key={i} data={p.data} />
 if (p.kind === 'TimeSeriesChart')
 return <TimeSeriesChart key={i} data={p.data} />
 if (p.kind === 'TableView') return <TableView key={i} data={p.data} />
 if (p.kind === 'AlertList') return <AlertList key={i} data={p.data} />
 if (p.kind === 'KanbanBoard') return <KanbanBoard key={i} data={p.data} />
 if (p.kind === 'ScheduleGrid') return <ScheduleGrid key={i} data={p.data} />
 if (p.kind === 'ConversationView') return <ConversationView key={i} data={p.data} />
 if (p.kind === 'SideBySideView') return <SideBySideView key={i} data={p.data} />
 if (p.kind === 'DocumentReader') return <DocumentReader key={i} data={p.data} />
 return null
 })}
 </div>
 ) : (
 <div
 style={{
 background: 'var(--bg)',
 border: '1px dashed var(--border)',
 borderRadius: 8,
 padding: 24,
 color: 'var(--text-muted)',
 fontSize: 13,
 lineHeight: 1.55,
 }}
 >
 <strong style={{ color: 'var(--text-2)' }}>
 No production output captured yet.
 </strong>
 <p style={{ margin: '8px 0 0' }}>
 When a trigger fires and the agent produces output against
 live data, the spec&rsquo;s declared views
 {unresolvedTypes.length > 0 ? (
 <>
 {' '}({unresolvedTypes.join(', ')})
 </>
 ) : null}{' '}
 render here. Eval-suite predictions are improvement-loop
 diagnostics and stay on{' '}
 <Link
 href={`/workspaces/${wsId}/workflows/${wfId}`}
 style={{ color: 'var(--accent)' }}
 >
 Overview
 </Link>
 .
 </p>
 </div>
 )}
 </section>
 </>
 )
}

interface InputItem {
 name: string
 description?: string
}

function InputBlock({
 label,
 hint,
 items,
}: {
 label: string
 hint: string
 items: InputItem[]
}) {
 return (
 <div className="operate-input-block">
 <div className="operate-input-head">
 <span className="operate-input-label">{label}</span>
 <span className="operate-input-hint">{hint}</span>
 </div>
 <ul className="operate-input-list">
 {items.map((it, i) => (
 <li key={`${it.name}-${i}`}>
 <code className="operate-input-name">{it.name}</code>
 {it.description ? (
 <span className="operate-input-desc"> · {it.description}</span>
 ) : null}
 </li>
 ))}
 </ul>
 </div>
 )
}
