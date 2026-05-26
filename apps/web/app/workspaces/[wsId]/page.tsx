import {
 getWorkflowIterations,
 kernelError,
 listWorkflows,
 type IterationList,
 type WorkflowList,
} from '../../../lib/api'
import {
 isStaleRunningIteration,
 relativeTime,
 workflowDisplayTitle,
 workspaceLabel,
} from '../../../lib/format'
import { LiftChart } from './lift-chart'
import { WorkflowsTable } from './workflows-table'

interface PageProps {
 params: Promise<{ wsId: string }>
}

// — Workspace Health page.
//
// Hero: LiftChart for the primary workflow (first one returned by
// `GET /api/workflows`, which sorts by `created_at ASC` so demand-
// prediction lands first when the bootstrap seed has run).
//
// Below: workflow-rows table over every workflow in the workspace.
//
// Failure modes: if the kernel API is unreachable (no DB, no API
// running), the page still renders with a clear "no data" message.
// `make web-dev` should work without `make api` per the existing
// the preview pattern.
export default async function WorkspaceHealthPage({ params }: PageProps) {
 const { wsId } = await params

 const wsLabel = workspaceLabel(wsId)

 let workflows: WorkflowList = { items: [], total: 0 }
 let primaryIterations: IterationList | null = null
 let apiError: { title: string; detail: string } | null = null

 try {
 workflows = await listWorkflows() } catch (err) {
 apiError = kernelError(err)
 }

 // Benchmarks (M5 / tau-bench) live in workflows table but are not
 // customer workflows. Carve them out of every Health-page count and
 // hero pick so the production picture stays clean; we surface a
 // small "loop validation" hint separately.
 const productionWorkflows = workflows.items.filter(
 (w) => w.kind !== 'benchmark',
 )
 const benchmarkWorkflows = workflows.items.filter(
 (w) => w.kind === 'benchmark',
 )

 // Primary workflow picked by signal, not creation order. Prefer
 // workflows with iterations recorded; among those, the one with the
 // most iterations wins (ties broken by best_ever_score, then id).
 // Falls back to the first workflow when nothing has run yet so the
 // page still has SOMETHING to anchor on.
 const primary = pickPrimary(productionWorkflows)
 try {
 if (primary) {
 primaryIterations = await getWorkflowIterations(primary.id)
 }
 } catch (err) {
 if (!apiError) apiError = kernelError(err)
 }
 // Workflows that have ever shipped at least one approved proposal
 // NOT a count of approval events (the API doesn't expose that yet).
 // Label below should match this scope. Benchmarks excluded.
 const improvedWorkflowsCount = productionWorkflows.reduce(
 (acc, w) => acc + (w.last_improved_at ? 1 : 0),
 0,
 )
 const pendingCount = productionWorkflows.reduce(
 (acc, w) => acc + w.pending_proposals_count,
 0,
 )
 const portfolioBest =
 primary && primary.best_ever_score !== null ? primary.best_ever_score : null
 const inFlightCount = productionWorkflows.reduce(
 (acc, w) => acc + (w.running_iteration_count ?? 0),
 0,
 )
 // Iterations still "running" past the stale threshold are flagged
 // separately so an abandoned run doesn't pollute the "fresh in flight"
 // banner. We only know the oldest running iteration per workflow, so
 // this is a workflow count, not a per-iteration count.
 const staleWorkflowCount = productionWorkflows.reduce(
 (acc, w) =>
 acc +
 ((w.running_iteration_count ?? 0) > 0 &&
 isStaleRunningIteration(w.oldest_running_started_at)
 ? 1
 : 0),
 0,
 )
 const oldestStaleStart = productionWorkflows.reduce<string | null>(
 (acc, w) => {
 if (!isStaleRunningIteration(w.oldest_running_started_at)) return acc
 const s = w.oldest_running_started_at ?? null
 if (!s) return acc
 if (!acc) return s
 return new Date(s).getTime() < new Date(acc).getTime() ? s : acc
 },
 null,
 )
 const isFirstTime = !apiError && productionWorkflows.length === 0

 return (
 <>
 <header className="page-header">
 <div>
 <h1 className="page-title">Workflow health</h1>
 <p className="page-subtitle">
 {wsLabel} · {productionWorkflows.length} workflow
 {productionWorkflows.length === 1 ? '' : 's'}
 {benchmarkWorkflows.length > 0
 ? ` · ${benchmarkWorkflows.length} benchmark${benchmarkWorkflows.length === 1 ? '' : 's'}`
 : ''}
 {primary ? ` · primary: ${primary.id}` : ''}
 </p>
 </div>
 <div className="page-actions">
 <a href={`/workspaces/${wsId}/workflows/new`} className="btn btn-primary">
 <svg className="btn-icon" viewBox="0 0 16 16">
 <path d="M8 3 L8 13 M3 8 L13 8" />
 </svg>
 New workflow
 </a>
 </div>
 </header>

 {apiError && (
 <div role="alert" className="api-banner" style={{ marginBottom: 24 }}>
 <strong>{apiError.title}</strong> {apiError.detail}
 </div>
 )}

 {isFirstTime && (
 <section className="empty-welcome">
 <h2 className="empty-welcome-title">Welcome to ownEvo.</h2>
 <p className="empty-welcome-body">
 No workflows in this workspace yet. ownEvo runs an improvement
 loop on the workflows that define how your business decides
 failures cluster, evals generate, proposals come back to you
 for review. To start, either:
 </p>
 <div className="empty-welcome-actions">
 <a
 href={`/workspaces/${wsId}/workflows/new`}
 className="btn btn-primary"
 >
 Describe a new workflow →
 </a>
 <a
 href={`/workspaces/${wsId}/workflows/connect`}
 className="btn btn-secondary"
 >
 Connect an agent you&rsquo;re already running →
 </a>
 </div>
 <p className="empty-welcome-hint">
 Try <code>make seed-demo</code> in the kernel root to load
 the demo workflows (credit-risk + contract-review).
 </p>
 </section>
 )}

 {inFlightCount > 0 && (
 <div className="inflight-banner" role="status">
 <span className="inflight-dot" />
 <strong>{inFlightCount}</strong> iteration
 {inFlightCount === 1 ? '' : 's'} in flight right now — refresh
 for updates.
 </div>
 )}

 {staleWorkflowCount > 0 && oldestStaleStart && (
 <div className="inflight-banner stale" role="status">
 <span className="inflight-dot stale" />
 <strong>{staleWorkflowCount}</strong> workflow
 {staleWorkflowCount === 1 ? '' : 's'} with a running iteration
 started {relativeTime(oldestStaleStart)} — may be abandoned.
 Open the workflow to retry or mark the run as failed.
 </div>
 )}

 {!isFirstTime && (
 <div className="metrics glance" style={{ marginBottom: 24 }}>
 <div className="metric">
 <div className="metric-label">Active workflows</div>
 <div className="metric-value">{productionWorkflows.length}</div>
 </div>
 <div className="metric">
 <div className="metric-label">Pending reviews</div>
 <div className="metric-value">{pendingCount}</div>
 </div>
 <div className="metric">
 <div className="metric-label">Workflows improved</div>
 <div className="metric-value">{improvedWorkflowsCount}</div>
 </div>
 <div className="metric">
 <div className="metric-label">Best val_score</div>
 <div className="metric-value">
 {portfolioBest !== null ? portfolioBest.toFixed(4) : '—'}
 </div>
 </div>
 </div>
 )}

 {primary && primaryIterations && (
 <section style={{ marginBottom: 32 }}>
 <div
 style={{
 display: 'flex',
 justifyContent: 'space-between',
 alignItems: 'baseline',
 marginBottom: 12,
 }}
 >
 <h2
 style={{
 fontSize: 13,
 fontWeight: 500,
 color: 'var(--text-2)',
 textTransform: 'uppercase',
 letterSpacing: '0.06em',
 }}
 >
 Lift — {workflowDisplayTitle(primary.id, primary.description, 60)}
 </h2>
 <a
 href={`/workspaces/${wsId}/workflows/${primary.id}`}
 style={{ fontSize: 12, color: 'var(--accent)' }}
 >
 Open workflow →
 </a>
 </div>
 <LiftChart points={primaryIterations.items} workflowId={primary.id} />
 </section>
 )}

 {!isFirstTime && (
 <section>
 <div
 style={{
 display: 'flex',
 justifyContent: 'space-between',
 alignItems: 'baseline',
 marginBottom: 10,
 }}
 >
 <h2
 style={{
 fontSize: 13,
 fontWeight: 500,
 color: 'var(--text-2)',
 textTransform: 'uppercase',
 letterSpacing: '0.06em',
 }}
 >
 Workflows
 </h2>
 </div>
 <WorkflowsTable workflows={productionWorkflows} wsId={wsId} />
 </section>
 )}

 {benchmarkWorkflows.length > 0 && (
 <section style={{ marginTop: 28 }}>
 <div
 style={{
 display: 'flex',
 justifyContent: 'space-between',
 alignItems: 'baseline',
 marginBottom: 10,
 }}
 >
 <h2
 style={{
 fontSize: 13,
 fontWeight: 500,
 color: 'var(--text-2)',
 textTransform: 'uppercase',
 letterSpacing: '0.06em',
 }}
 >
 Loop validation · benchmarks
 </h2>
 <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
 Kernel proof runs — not customer workflows
 </span>
 </div>
 <WorkflowsTable workflows={benchmarkWorkflows} wsId={wsId} />
 </section>
 )}
 </>
 )
}

// Primary-workflow picker. The Health hero (lift chart) shows the
// workflow with the strongest signal — most iterations recorded, ties
// broken by best_ever_score, then by id for determinism. Falls back to
// the first workflow when nothing has run yet so the table below the
// hero is consistent with the hero (workflows[0] === pickPrimary's
// fallback).
function pickPrimary(
 workflows: WorkflowList['items'],
): WorkflowList['items'][number] | undefined {
 if (workflows.length === 0) return undefined
 const withIter = workflows.filter((w) => w.iteration_count > 0)
 if (withIter.length === 0) return workflows[0]
 return [...withIter].sort((a, b) => {
 if (b.iteration_count !== a.iteration_count) {
 return b.iteration_count - a.iteration_count
 }
 const aScore = a.best_ever_score ?? 0
 const bScore = b.best_ever_score ?? 0
 if (bScore !== aScore) return bScore - aScore
 return a.id.localeCompare(b.id)
 })[0]
}
