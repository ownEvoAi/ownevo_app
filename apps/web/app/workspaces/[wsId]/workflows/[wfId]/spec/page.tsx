import { notFound } from 'next/navigation'
import {
 getWorkflowAnatomy,
 getWorkflowSkills,
 KernelApiError,
 type SkillSummary,
 type WorkflowAnatomy,
} from '@/lib/api'
import {
 MetricSection,
 PrimitivesSection,
 simulatorMeta,
 SimulatorSection,
} from '@/app/components/workflow-spec-sections'
import { InlineDescriptionBlock } from '../inline-description-edit'
import { ProposeMetricEdit } from './propose-metric-edit'
import { ProposeSimEdit } from './propose-sim-edit'
import { ProposeUIPrimitiveEdit } from './propose-ui-primitive-edit'

interface PageProps {
 params: Promise<{ wsId: string; wfId: string }>
}

// Post-creation Spec tab — the artifact view rendered against the live
// workflow 30+ days after authoring. Agent environment (tools / personas
// / data sources / env generators) + Success metric + Operate-view UI
// primitives, every section editable through the regression gate. The
// description is editable in place (cosmetic; no gate flow).
//
// "Agent environment" used to be labelled "Simulator" in the UI; the
// proposal kind in the DB is still `sim` for storage continuity. The
// replay sim (workflows.simulation_plan — init_state_code / step_code
// that eval cases bind to) has no UI surface and is frozen at NL-gen
// time. See approvals/apply.py for the invariant.
//
// Eval cases are intentionally NOT rendered here — they live on the
// dedicated Eval cases tab where they pair with generate-from-failures
// and ad-hoc CRUD. The authoring `/new/review/[wfId]` page still shows
// them inline because that surface predates the per-workflow tab strip.
export default async function WorkflowSpecPage({ params }: PageProps) {
 const { wsId, wfId } = await params

 let anatomy: WorkflowAnatomy | null = null
 let skills: SkillSummary[] = []
 let apiError: { title: string; detail: string } | null = null

 try {
 const [anatomyRes, skillList] = await Promise.all([
 getWorkflowAnatomy(wfId),
 getWorkflowSkills(wfId),
 ])
 anatomy = anatomyRes
 skills = skillList.items
 } catch (err) {
 if (err instanceof KernelApiError && err.status === 404) {
 notFound }
 apiError = {
 title: 'Could not load workflow.',
 detail: err instanceof Error ? err.message : String(err),
 }
 }

 if (apiError || !anatomy) {
 return (
 <div role="alert" className="api-banner">
 <strong>{apiError?.title ?? 'Workflow unavailable.'}</strong>{' '}
 {apiError?.detail ?? ''}
 </div>
 )
 }

 const operateHref = `/workspaces/${wsId}/workflows/${wfId}/operate`
 const description = anatomy.description ?? ''
 const spec = anatomy.spec
 const tools = spec.tools ?? []
 const personas = spec.environment?.personas ?? []
 const envGenerators = spec.environment?.env_generators ?? []
 const dataSources = spec.environment?.data_sources ?? []
 const primitives = spec.ui?.tabs?.[0]?.primitives ?? []
 const metricDef = anatomy.metric_definition ?? null
 const simPlan = anatomy.simulation_plan ?? null
 const skillCount = skills.length
 const simMeta = simulatorMeta(
 tools,
 personas,
 envGenerators,
 dataSources,
 simPlan !== null,
 )

 return (
 <>
 <header className="spec-tab-head">
 <div>
 <h2 className="section-title">Workflow spec</h2>
 <p className="spec-tab-sub">
 The generated artifacts that define this workflow. Edits to
 the description are cosmetic; edits to the agent
 environment, success metric, or operate-view primitives
 flow through the regression gate. Eval cases live on their
 own tab.
 </p>
 </div>
 </header>

 {description ? (
 <InlineDescriptionBlock
 wsId={wsId}
 wfId={wfId}
 description={description}
 />
 ) : null}

 <SimulatorSection
 meta={simMeta}
 tools={tools}
 personas={personas}
 envGenerators={envGenerators}
 dataSources={dataSources}
 action={
 <ProposeSimEdit
 wsId={wsId}
 wfId={wfId}
 tools={tools}
 personas={personas}
 envGenerators={envGenerators}
 dataSources={dataSources}
 />
 }
 />

 <MetricSection
 metric={metricDef}
 action={
 <ProposeMetricEdit wsId={wsId} wfId={wfId} current={metricDef} />
 }
 />

 <PrimitivesSection
 primitives={primitives}
 operateHref={operateHref}
 skillCount={skillCount}
 action={
 <ProposeUIPrimitiveEdit
 wsId={wsId}
 wfId={wfId}
 current={primitives as Array<{ type: string }>}
 />
 }
 />

 <p
 className="spec-tab-sub"
 style={{ marginTop: 24, fontSize: 13 }}
 >
 Looking for eval cases? They live on the{' '}
 <a href={`/workspaces/${wsId}/workflows/${wfId}/eval-cases`}>
 Eval cases tab
 </a>{' '}
 — that's where you can generate cases from clustered failures,
 add cases by hand, or delete stale ones.
 </p>
 </>
 )
}
