import { notFound } from 'next/navigation'
import {
  type EvalCaseSummary,
  getWorkflowAnatomy,
  getWorkflowSkills,
  KernelApiError,
  listWorkflowEvalCases,
  type SkillSummary,
  type WorkflowAnatomy,
} from '@/lib/api'
import {
  EvalCasesSection,
  MetricSection,
  PrimitivesSection,
  simulatorMeta,
  SimulatorSection,
} from '@/app/components/workflow-spec-sections'
import { InlineDescriptionBlock } from '../inline-description-edit'
import { ProposeMetricEdit } from './propose-metric-edit'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Post-creation Spec tab — the same artifact view a reviewer sees at
// authoring time (`/workflows/new/review/[wfId]`) but rendered against
// the live workflow surface 30+ days later. Simulator + Eval cases +
// Success metric + Operate-view UI primitives, every section editable.
//
// Read-only on the artifacts in this slice; the Edit affordances flow
// through the regression gate as part of the next slice. The header
// description IS editable in place — the existing
// `updateDescriptionAction` is reused for an inline-edit, since
// description edits are cosmetic on their own (they don't regenerate
// the spec / sim / eval / metric).
export default async function WorkflowSpecPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let anatomy: WorkflowAnatomy | null = null
  let skills: SkillSummary[] = []
  let evalCases: EvalCaseSummary[] = []
  let apiError: { title: string; detail: string } | null = null

  try {
    const [anatomyRes, skillList, evalList] = await Promise.all([
      getWorkflowAnatomy(wfId),
      getWorkflowSkills(wfId),
      listWorkflowEvalCases(wfId),
    ])
    anatomy = anatomyRes
    skills = skillList.items
    evalCases = evalList.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound()
    }
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
            the description are cosmetic; edits to the simulator,
            success metric, eval cases, or operate-view primitives flow
            through the regression gate.
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
        designLog={anatomy.design_agent_log ?? null}
      />

      <EvalCasesSection cases={evalCases} wsId={wsId} wfId={wfId} />

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
      />
    </>
  )
}
