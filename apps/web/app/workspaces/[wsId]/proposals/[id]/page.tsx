import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
 getOrderingInversionCheck,
 getProposal,
 getWorkflowAnatomy,
 KernelApiError,
 type GateResultCases,
 type OrderingInversionCheck,
 type ProposalDetail,
} from '@/lib/api'
import { formatDateTime, formatScore, relativeTime } from '@/lib/format'
import { SkillDiff } from '@/app/components/skill-diff'
import { isDemoMode } from '@/lib/demo-mode'
import { WorkflowTabs } from '@/app/workspaces/[wsId]/workflows/[wfId]/workflow-tabs'
import { DecideForm } from './decide-form'
import { DeployForm } from './deploy-form'
import { ShipCopilotStudioForm } from './ship-copilot-studio-form'
import { ShipLangSmithForm } from './ship-langsmith-form'

interface PageProps {
 params: Promise<{ wsId: string; id: string }>
}

const CHECK_ICON_PATH = 'M3 8 L7 12 L13 4'
const X_ICON_PATH = 'M4 4 L12 12 M12 4 L4 12'
const EXCLAM_ICON_PATH = 'M8 3 L8 9 M8 11.5 L8 12.5'

// (7.1.4) — proposal detail under the workspace shell.
// Inherits the workspace sidebar from `app/workspaces/[wsId]/layout.tsx`,
// breadcrumb chain links back through the workspace + the workflow's
// Failures view (the typical entry point now that FailureClusterCard
// routes here when `latest_proposal_id` is non-null).
//
// Visual target:
// Body shape unchanged from this surface — the diff vs the legacy
// page is the breadcrumb chain + revalidatePath target.
export default async function ProposalDetailPage({ params }: PageProps) {
 const { wsId, id } = await params

 let proposal: ProposalDetail
 try {
 proposal = await getProposal(id)
 } catch (err) {
 if (err instanceof KernelApiError && err.status === 404) {
 notFound }
 throw err
 }

 const canDecide = proposal.state === 'gate-passed'
 const canDeploy = proposal.state === 'approved-awaiting-deploy'
 // Rollback today only supports kind='skill' — the deploy_proposal
 // helper walks the audit chain for prior skill_versions. Non-skill
 // rollback is "create a new proposal with the prior value"
 // (separate UX), so we hide the button rather than offer a 5xx.
 const canRollback =
 proposal.state === 'deployed' && proposal.kind === 'skill'
 const wfRoot = `/workspaces/${wsId}/workflows/${proposal.workflow.id}`

 // Resolve isBenchmark + (for ui-primitive proposals) the current
 // primitive list so the diff can show added/removed types. Soft-
 // fail to defaults on 404 — the page still renders, just without
 // the side-by-side primitive comparison.
 let isBenchmark = false
 let currentPrimitives: Array<{ type: string }> = []
 let currentSpec: Record<string, unknown> | null = null
 try {
 const anatomy = await getWorkflowAnatomy(proposal.workflow.id)
 isBenchmark = anatomy.kind === 'benchmark'
 const prims = anatomy.spec?.ui?.tabs?.[0]?.primitives ?? []
 currentPrimitives = prims.filter(
 (p): p is { type: string } =>
 typeof p === 'object' &&
 p !== null &&
 typeof (p as { type?: unknown }).type === 'string',
 )
 currentSpec = (anatomy.spec as unknown as Record<string, unknown>) ?? null
 } catch {
 /* ignore — fall through with isBenchmark=false */
 }

 // For kind='metric' proposals, fetch the ordering-inversion check.
 // Soft-fail: a check failure shouldn't block the proposal detail
 // from rendering — the panel falls through to an "unavailable"
 // state if anything goes wrong.
 let inversionCheck: OrderingInversionCheck | null = null
 if (proposal.kind === 'metric') {
 try {
 inversionCheck = await getOrderingInversionCheck(proposal.id)
 } catch {
 /* ignore — panel renders as unavailable */
 }
 }

 return (
 <div>
 <nav className="crumb-row">
 <Link href={`/workspaces/${wsId}`}>Workspace</Link>
 <span className="sep">/</span>
 <Link href={`${wfRoot}/proposals`}>
 {proposal.workflow.description}
 </Link>
 <span className="sep">/</span>
 <span>Proposal {proposal.id.slice(0, 8)}</span>
 </nav>

 <WorkflowTabs
 wsId={wsId}
 wfId={proposal.workflow.id}
 isBenchmark={isBenchmark}
 activeOverride="proposals"
 />

 <ProposalHeader proposal={proposal} />

 <div className="prop-grid">
 <div>
 {proposal.kind === 'skill' ? (
 <>
 <h2 className="section-title">
 Skill diff · {proposal.skill_id}
 {proposal.parent_version_seq !== null
 ? ` v${proposal.parent_version_seq} → v${proposal.parent_version_seq + 1}`
 : ' · initial version'}
 </h2>
 <SkillDiff
 current={proposal.parent_version_content}
 proposed={proposal.proposed_content}
 parentVersionSeq={proposal.parent_version_seq}
 />
 </>
 ) : (
 <>
 <ArtifactDiff
 proposal={proposal}
 currentPrimitives={currentPrimitives}
 currentSpec={currentSpec}
 />
 {proposal.kind === 'metric' && (
 <OrderingInversionPanel check={inversionCheck} />
 )}
 </>
 )}

 <h2 className="section-title">Why this change</h2>
 <Rationale proposal={proposal} />

 {proposal.audit_entries.length > 0 && (
 <>
 <h2 className="section-title">Audit chain</h2>
 <AuditList entries={proposal.audit_entries} />
 </>
 )}
 </div>

 <aside>
 <GateResult proposal={proposal} />
 {proposal.expected_impact &&
 Object.keys(proposal.expected_impact).length > 0 && (
 <ExpectedImpact impact={proposal.expected_impact} />
 )}
 {canDecide ? (
 <DecideForm
 proposalId={proposal.id}
 wsId={wsId}
 demoMode={isDemoMode }
 />
 ) : (
 <DecisionRecorded proposal={proposal} />
 )}
 {(canDeploy || canRollback) && (
 <DeployForm
 proposalId={proposal.id}
 wsId={wsId}
 workflowId={proposal.workflow.id}
 state={canDeploy ? 'approved-awaiting-deploy' : 'deployed'}
 kind={proposal.kind}
 demoMode={isDemoMode }
 />
 )}
 {proposal.state === 'deployed' && proposal.kind === 'skill' && (
 <ShipLangSmithForm
 proposalId={proposal.id}
 wsId={wsId}
 demoMode={isDemoMode }
 />
 )}
 {proposal.state === 'deployed' && proposal.kind === 'skill' && (
 <ShipCopilotStudioForm
 proposalId={proposal.id}
 wsId={wsId}
 demoMode={isDemoMode }
 />
 )}
 </aside>
 </div>
 </div>
 )
}

function ProposalHeader({ proposal }: { proposal: ProposalDetail }) {
 return (
 <div className="prop-header">
 <div className="prop-pills">
 <span className={`pill ${pillVariant(proposal.state)}`}>
 {proposal.state}
 </span>
 {proposal.iteration.sandbox_error_class && (
 <span className="pill amber">
 sandbox: {proposal.iteration.sandbox_error_class}
 </span>
 )}
 <span className="pill outline">
 iter #{proposal.iteration.iteration_index}
 </span>
 </div>
 <h1 className="prop-title">{proposal.plain_language_summary}</h1>
 <div className="prop-meta-row">
 <Meta label="Workflow" value={proposal.workflow.description} />
 {proposal.kind === 'skill' && proposal.skill_id ? (
 <Meta label="Skill" value={proposal.skill_id} />
 ) : (
 <Meta label="Artifact" value={artifactLabel(proposal.kind)} />
 )}
 <Meta
 label="Created"
 value={`${relativeTime(proposal.created_at)} · ${formatDateTime(proposal.created_at)}`}
 />
 <Meta label="Workflow mode" value={proposal.workflow.mode} />
 </div>
 </div>
 )
}

function artifactLabel(kind: ProposalDetail['kind']): string {
 switch (kind) {
 case 'description':
 return 'Description'
 case 'metric':
 return 'Success metric'
 case 'sim':
 return 'Agent environment'
 case 'ui-primitive':
 return 'Operate-view UI'
 default:
 return kind
 }
}

// 9.2.3 — per-kind diff renderer for non-skill artifact proposals.
// Renders a kind-specific summary of the proposed change. Metric and
// ui-primitive have dedicated branches; description / sim fall back
// to a pretty-printed payload until their flows ship.
function ArtifactDiff({
 proposal,
 currentPrimitives,
 currentSpec,
}: {
 proposal: ProposalDetail
 currentPrimitives: Array<{ type: string }>
 currentSpec: Record<string, unknown> | null
}) {
 const payload = proposal.proposed_payload ?? {}
 if (proposal.kind === 'ui-primitive') {
 return (
 <UIPrimitiveDiff
 payload={payload}
 currentPrimitives={currentPrimitives}
 />
 )
 }
 if (proposal.kind === 'description') {
 return <DescriptionDiff payload={payload} />
 }
 if (proposal.kind === 'sim') {
 return <SimDiff payload={payload} currentSpec={currentSpec} />
 }
 if (proposal.kind === 'metric') {
 const name = stringOrNull(payload.name) ?? '(unnamed)'
 const family = stringOrNull(payload.family)
 const direction = stringOrNull(payload.direction)
 const description = stringOrNull(payload.description)
 const rationale = stringOrNull(payload.rationale)
 const metaLine = [family, direction].filter(Boolean).join(' · ')
 return (
 <>
 <h2 className="section-title">
 Success metric · proposed change
 </h2>
 <div className="artifact-diff metric-def">
 <div>
 <span className="key">metric:</span> {name}
 </div>
 {metaLine ? (
 <div className="artifact-diff-meta">{metaLine}</div>
 ) : null}
 {description ? (
 <div style={{ marginTop: 6, color: 'var(--text-3)' }}>
 {description}
 </div>
 ) : null}
 {rationale ? (
 <div style={{ marginTop: 4, color: 'var(--text-3)' }}>
 <span className="key">rationale:</span> {rationale}
 </div>
 ) : null}
 </div>
 </>
 )
 }
 return (
 <>
 <h2 className="section-title">
 {artifactLabel(proposal.kind)} · proposed change
 </h2>
 <pre className="artifact-diff-payload">
 {JSON.stringify(payload, null, 2)}
 </pre>
 </>
 )
}

function stringOrNull(v: unknown): string | null {
 return typeof v === 'string' && v.length > 0 ? v : null
}

function Meta({ label, value }: { label: string; value: string }) {
 return (
 <div className="prop-meta">
 <span className="prop-meta-label">{label}</span>
 <span className="prop-meta-value">{value}</span>
 </div>
 )
}

function pillVariant(state: string): string {
 if (state === 'gate-passed') return 'accent'
 if (state === 'approved-awaiting-deploy' || state === 'deployed') return 'green'
 if (state === 'rejected') return 'red'
 if (state === 'gate-failed' || state === 'changes-requested') return 'amber'
 return 'outline'
}

function GateResult({ proposal }: { proposal: ProposalDetail }) {
 const cases = proposal.gate_result_cases
 const verdict = gateVerdict(proposal, cases)

 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Regression gate</div>

 <div className="gate-headline">
 <div className={`gate-icon ${verdict.tone}`}>
 <svg viewBox="0 0 16 16" aria-hidden>
 <path d={verdict.iconPath} />
 </svg>
 </div>
 <div>
 <div className="gate-status-text">{verdict.headline}</div>
 <div className="gate-status-meta">
 val_score: {formatScore(proposal.eval_score)} · best_ever{' '}
 {formatScore(proposal.iteration.best_ever_score_after)}
 </div>
 </div>
 </div>

 {cases && !cases.unknown && <CaseBreakdown cases={cases} />}

 {proposal.eval_rationale && (
 <p
 style={{
 fontSize: 12.5,
 color: 'var(--text-3)',
 marginTop: 12,
 lineHeight: 1.55,
 }}
 >
 {proposal.eval_rationale}
 </p>
 )}
 </div>
 )
}

function gateVerdict(
 proposal: ProposalDetail,
 cases: GateResultCases | null,
): { headline: string; tone: '' | 'amber' | 'red'; iconPath: string } {
 if (proposal.iteration.sandbox_error_class) {
 return {
 headline: `Sandbox: ${proposal.iteration.sandbox_error_class}`,
 tone: 'amber',
 iconPath: EXCLAM_ICON_PATH,
 }
 }
 if (proposal.state === 'gate-failed') {
 const regressed = cases?.regressed.length ?? 0
 return {
 headline: regressed > 0 ? `${regressed} regression(s)` : 'Gate failed',
 tone: 'red',
 iconPath: EXCLAM_ICON_PATH,
 }
 }
 // FAIL_REGRESSION and FAIL_NO_IMPROVEMENT both land on 'rejected' (not
 // 'gate-failed'). Without this check they fall through to the green path.
 if (proposal.state === 'rejected') {
 const regressed = cases?.regressed.length ?? 0
 return {
 headline: regressed > 0 ? `${regressed} regression(s)` : 'Gate rejected',
 tone: 'red',
 iconPath: EXCLAM_ICON_PATH,
 }
 }
 const passed = cases?.passed.length ?? 0
 const total = passed + (cases?.regressed.length ?? 0)
 if (total > 0) {
 return {
 headline: `${passed} / ${total} prior cases pass`,
 tone: '',
 iconPath: CHECK_ICON_PATH,
 }
 }
 return { headline: 'Gate passed', tone: '', iconPath: CHECK_ICON_PATH }
}

function CaseBreakdown({ cases }: { cases: GateResultCases }) {
 const sections: { label: string; rows: string[]; cls: string }[] = []
 if (cases.regressed.length > 0) {
 sections.push({
 label: `Regressed (${cases.regressed.length})`,
 rows: cases.regressed,
 cls: 'fail',
 })
 }
 if (cases.passed.length > 0) {
 sections.push({
 label: `Passed (${cases.passed.length})`,
 rows: cases.passed,
 cls: '',
 })
 }
 if (cases.newly_admitted.length > 0) {
 sections.push({
 label: `Newly admitted (${cases.newly_admitted.length})`,
 rows: cases.newly_admitted,
 cls: 'new',
 })
 }

 if (sections.length === 0) {
 return (
 <p
 style={{
 fontSize: 12,
 color: 'var(--text-muted)',
 marginTop: 6,
 }}
 >
 Gate had no prior eval cases yet (bootstrap iteration).
 </p>
 )
 }

 return (
 <div className="gate-list">
 {sections.map((section) => (
 <div key={section.label}>
 <div className="gate-section-label">{section.label}</div>
 {section.rows.map((row) => (
 <div
 key={`${section.label}:${row}`}
 className={`gate-case ${section.cls}`}
 >
 <span className="check">
 <svg viewBox="0 0 16 16" aria-hidden>
 <path d={section.cls === 'fail' ? X_ICON_PATH : CHECK_ICON_PATH} />
 </svg>
 </span>
 <span className="case-name" title={row}>
 {row}
 </span>
 </div>
 ))}
 </div>
 ))}
 </div>
 )
}

function ExpectedImpact({ impact }: { impact: Record<string, unknown> }) {
 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Expected impact</div>
 <div className="impact-grid">
 {Object.entries(impact).map(([k, v]) => (
 <div key={k} className="impact-cell">
 <div className="impact-label">{k}</div>
 <div className="impact-value">{String(v)}</div>
 </div>
 ))}
 </div>
 </div>
 )
}

function DecisionRecorded({ proposal }: { proposal: ProposalDetail }) {
 if (!proposal.approval) {
 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Decision</div>
 <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 8 }}>
 Proposal is in <code>{proposal.state}</code> — not awaiting review.
 </p>
 </div>
 )
 }
 const decisionLabel =
 proposal.approval.decision === 'approve'
 ? 'Approved'
 : proposal.approval.decision === 'request-changes'
 ? 'Changes requested'
 : 'Rejected'
 return (
 <div className="sidebar-card">
 <div className="sidebar-title">{decisionLabel}</div>
 <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500 }}>
 {proposal.approval.decided_by}
 </div>
 <div
 style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}
 >
 {proposal.approval.approver_type} ·{' '}
 {formatDateTime(proposal.approval.decided_at)}
 </div>
 {proposal.approval.comment && (
 <p
 style={{
 fontSize: 13,
 color: 'var(--text-2)',
 marginTop: 10,
 lineHeight: 1.5,
 background: 'var(--surface)',
 padding: '8px 10px',
 borderRadius: 5,
 }}
 >
 {proposal.approval.comment}
 </p>
 )}
 {proposal.approval.became_eval_case_id && (
 <p
 style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 8 }}
 >
 Comment recorded as eval case{' '}
 <code>{proposal.approval.became_eval_case_id.slice(0, 8)}</code>{' '}
 (provenance: rejected-feedback).
 </p>
 )}
 </div>
 )
}

function Rationale({ proposal }: { proposal: ProposalDetail }) {
 return (
 <div className="rationale">
 <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.6 }}>
 {proposal.plain_language_summary}
 </p>
 {proposal.eval_rationale && (
 <p
 style={{
 fontSize: 12.5,
 color: 'var(--text-muted)',
 marginTop: 10,
 lineHeight: 1.5,
 fontFamily: 'ui-monospace, monospace',
 }}
 >
 gate: {proposal.eval_rationale}
 </p>
 )}
 </div>
 )
}

function AuditList({ entries }: { entries: ProposalDetail['audit_entries'] }) {
 return (
 <ol
 style={{
 listStyle: 'none',
 padding: 0,
 margin: 0,
 background: 'var(--bg)',
 border: '1px solid var(--border)',
 borderRadius: 8,
 boxShadow: 'var(--shadow-sm)',
 }}
 >
 {entries.map((e, i) => (
 <li
 key={e.id}
 style={{
 padding: '12px 16px',
 borderBottom:
 i < entries.length - 1 ? '1px solid var(--border)' : 'none',
 display: 'flex',
 justifyContent: 'space-between',
 alignItems: 'baseline',
 gap: 12,
 }}
 >
 <div>
 <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>
 {e.kind}
 </div>
 <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}>
 {e.actor}
 </div>
 </div>
 <div
 style={{
 fontSize: 11,
 color: 'var(--text-muted)',
 fontVariantNumeric: 'tabular-nums',
 }}
 >
 {formatDateTime(e.created_at)} · seq {e.seq}
 </div>
 </li>
 ))}
 </ol>
 )
}

// 9.2.3 — ordering-inversion check for kind='metric' proposals. Lands
// above the approval form so a reviewer sees the consequence of
// switching the metric (which prior iterations would flip pass/fail
// under the new metric) before they click approve.
function OrderingInversionPanel({
 check,
}: {
 check: OrderingInversionCheck | null
}) {
 if (check === null) {
 return (
 <div className="inversion-panel inversion-unavailable">
 <strong>Ordering-inversion check unavailable.</strong> The
 kernel didn&apos;t return a result for this proposal — try
 refreshing.
 </div>
 )
 }
 if (check.status !== 'ok') {
 return (
 <div className="inversion-panel inversion-unavailable">
 <strong>Ordering-inversion check unavailable.</strong>{' '}
 {check.reason ?? 'No reason supplied.'}
 </div>
 )
 }

 const nInverted = check.n_inverted
 const nIterations = check.iterations.length

 return (
 <div
 className={`inversion-panel ${nInverted > 0 ? 'inversion-warn' : 'inversion-ok'}`}
 >
 <h3 className="inversion-title">
 Ordering-inversion check ·{' '}
 <code>{check.current_metric_family}</code> →{' '}
 <code>{check.proposed_metric_family}</code>
 </h3>
 <p className="inversion-headline">
 {nInverted === 0 ? (
 <>
 Re-scored {nIterations} iteration
 {nIterations === 1 ? '' : 's'} under the proposed metric
 no gate verdicts flip.
 </>
 ) : (
 <>
 <strong>
 {nInverted} of {nIterations} iteration
 {nIterations === 1 ? '' : 's'} would flip pass/fail
 </strong>{' '}
 under the proposed metric. Review the per-iteration deltas
 before approving.
 </>
 )}
 </p>

 <table className="inversion-table">
 <thead>
 <tr>
 <th>Iter</th>
 <th>Cases</th>
 <th>
 <code>{check.current_metric_family}</code>
 </th>
 <th>
 <code>{check.proposed_metric_family}</code>
 </th>
 <th>Δ</th>
 <th>Old verdict</th>
 <th>New verdict</th>
 </tr>
 </thead>
 <tbody>
 {check.iterations.map((it) => (
 <tr
 key={it.iteration_index}
 className={it.inverted ? 'inversion-row-flip' : ''}
 >
 <td>#{it.iteration_index}</td>
 <td>{it.n_cases}</td>
 <td>{formatScoreOrDash(it.old_score)}</td>
 <td>{formatScoreOrDash(it.new_score)}</td>
 <td>{formatDeltaOrDash(it.delta)}</td>
 <td>{verdictPill(it.old_meets_target)}</td>
 <td>{verdictPill(it.new_meets_target)}</td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )
}

function formatScoreOrDash(v: number | null): string {
 return formatScore(v, 3)
}

function formatDeltaOrDash(v: number | null): string {
 if (v === null) return '—'
 const sign = v > 0 ? '+' : ''
 return `${sign}${v.toFixed(3)}`
}

function verdictPill(meets: boolean | null) {
 if (meets === null) return <span className="failures-list-muted">—</span>
 if (meets)
 return <span className="pill source-prod">passes</span>
 return <span className="pill red">fails</span>
}

// 9.2.3 — diff renderer for kind='description' proposals. Shows the
// previous and proposed text side-by-side; the kernel-side payload
// carries both so the page doesn't need a second fetch.
function DescriptionDiff({
 payload,
}: {
 payload: Record<string, unknown>
}) {
 const proposed =
 typeof payload.description === 'string' ? payload.description : ''
 const previous =
 typeof payload.previous_description === 'string'
 ? payload.previous_description
 : ''
 const charDelta = proposed.length - previous.length
 return (
 <>
 <h2 className="section-title">Description · proposed change</h2>
 <div className="description-diff-meta">
 {charDelta === 0 ? (
 <>0 net character change</>
 ) : charDelta > 0 ? (
 <>+{charDelta} characters</>
 ) : (
 <>{charDelta} characters</>
 )}
 </div>
 <div className="description-diff-grid">
 <div className="description-diff-col">
 <div className="description-diff-head removed">Current</div>
 <pre className="description-diff-body">{previous || '(empty)'}</pre>
 </div>
 <div className="description-diff-col">
 <div className="description-diff-head added">Proposed</div>
 <pre className="description-diff-body">{proposed || '(empty)'}</pre>
 </div>
 </div>
 </>
 )
}

// 9.2.3 — diff renderer for kind='sim' proposals. Computes added /
// removed entities across the four sim sections (tools, personas,
// data_sources, env_generators) by their identifier field (`name`
// for tools / generators, `role` for personas, `id` for data
// sources). Unchanged entities aren't listed — only the deltas, so
// the reviewer sees the impact at a glance.
function SimDiff({
 payload,
 currentSpec,
}: {
 payload: Record<string, unknown>
 currentSpec: Record<string, unknown> | null
}) {
 const sections: Array<{
 key: string
 label: string
 idField: string
 }> = [
 { key: 'tools', label: 'Tools', idField: 'name' },
 { key: 'personas', label: 'Personas', idField: 'role' },
 { key: 'data_sources', label: 'Data sources', idField: 'id' },
 { key: 'env_generators', label: 'Environment generators', idField: 'name' },
 ]
 const currentEnv =
 (currentSpec?.environment as Record<string, unknown> | undefined) ?? {}
 const currentBySection: Record<string, unknown> = {
 tools: currentSpec?.tools ?? [],
 personas: currentEnv.personas ?? [],
 data_sources: currentEnv.data_sources ?? [],
 env_generators: currentEnv.env_generators ?? [],
 }

 function idsFor(value: unknown, idField: string): string[] {
 if (!Array.isArray(value)) return []
 return value
 .map((v) => {
 if (typeof v !== 'object' || v === null) return null
 const id = (v as Record<string, unknown>)[idField]
 return typeof id === 'string' ? id : null
 })
 .filter((s): s is string => s !== null)
 }

 return (
 <>
 <h2 className="section-title">Agent environment · proposed change</h2>
 <div className="artifact-diff sim-diff">
 {sections.map((s) => {
 const currIds = new Set(idsFor(currentBySection[s.key], s.idField))
 const propIds = new Set(idsFor(payload[s.key], s.idField))
 // No section in the proposal → not changed.
 if (!(s.key in payload)) return null
 const added = [...propIds].filter((id) => !currIds.has(id))
 const removed = [...currIds].filter((id) => !propIds.has(id))
 if (added.length === 0 && removed.length === 0) {
 return (
 <div key={s.key} className="sim-diff-section">
 <div className="sim-diff-section-head">{s.label}</div>
 <div className="ui-primitive-diff-note">
 No additions or removals by identifier — the proposal
 may update props on existing entries.
 </div>
 </div>
 )
 }
 return (
 <div key={s.key} className="sim-diff-section">
 <div className="sim-diff-section-head">{s.label}</div>
 {added.length > 0 ? (
 <div className="ui-primitive-diff-row">
 <span className="ui-primitive-diff-label added">
 Added · {added.length}
 </span>
 <div className="ui-primitive-diff-pills">
 {added.map((id) => (
 <span
 key={id}
 className="pill source-prod ui-primitive-pill"
 >
 + {id}
 </span>
 ))}
 </div>
 </div>
 ) : null}
 {removed.length > 0 ? (
 <div className="ui-primitive-diff-row">
 <span className="ui-primitive-diff-label removed">
 Removed · {removed.length}
 </span>
 <div className="ui-primitive-diff-pills">
 {removed.map((id) => (
 <span
 key={id}
 className="pill red ui-primitive-pill"
 >
 − {id}
 </span>
 ))}
 </div>
 </div>
 ) : null}
 </div>
 )
 })}
 </div>
 </>
 )
}

// 9.2.3 — diff renderer for kind='ui-primitive' proposals. Compares
// the current primitive list against `payload.primitives`, shows
// the unchanged / added / removed types with shape-pills.
function UIPrimitiveDiff({
 payload,
 currentPrimitives,
}: {
 payload: Record<string, unknown>
 currentPrimitives: Array<{ type: string }>
}) {
 const proposedRaw = payload.primitives
 const proposed: Array<{ type: string }> = Array.isArray(proposedRaw)
 ? proposedRaw.filter(
 (p): p is { type: string } =>
 typeof p === 'object' &&
 p !== null &&
 typeof (p as { type?: unknown }).type === 'string',
 )
 : []

 const currentSet = new Set(currentPrimitives.map((p) => p.type))
 const proposedSet = new Set(proposed.map((p) => p.type))
 const added = proposed.filter((p) => !currentSet.has(p.type))
 const removed = currentPrimitives.filter((p) => !proposedSet.has(p.type))
 const unchanged = currentPrimitives.filter((p) => proposedSet.has(p.type))

 return (
 <>
 <h2 className="section-title">Operate-view UI · proposed change</h2>
 <div className="artifact-diff ui-primitive-diff">
 {added.length === 0 && removed.length === 0 ? (
 <div className="ui-primitive-diff-note">
 No primitive types added or removed. The proposal may
 update per-primitive props on existing types.
 </div>
 ) : (
 <>
 {added.length > 0 ? (
 <div className="ui-primitive-diff-row">
 <span className="ui-primitive-diff-label added">
 Added · {added.length}
 </span>
 <div className="ui-primitive-diff-pills">
 {added.map((p) => (
 <span
 key={p.type}
 className="pill source-prod ui-primitive-pill"
 >
 + {p.type}
 </span>
 ))}
 </div>
 </div>
 ) : null}
 {removed.length > 0 ? (
 <div className="ui-primitive-diff-row">
 <span className="ui-primitive-diff-label removed">
 Removed · {removed.length}
 </span>
 <div className="ui-primitive-diff-pills">
 {removed.map((p) => (
 <span
 key={p.type}
 className="pill red ui-primitive-pill"
 >
 − {p.type}
 </span>
 ))}
 </div>
 </div>
 ) : null}
 </>
 )}
 {unchanged.length > 0 ? (
 <div className="ui-primitive-diff-row">
 <span className="ui-primitive-diff-label unchanged">
 Unchanged · {unchanged.length}
 </span>
 <div className="ui-primitive-diff-pills">
 {unchanged.map((p) => (
 <span
 key={p.type}
 className="pill outline ui-primitive-pill"
 >
 {p.type}
 </span>
 ))}
 </div>
 </div>
 ) : null}
 </div>
 </>
 )
}
