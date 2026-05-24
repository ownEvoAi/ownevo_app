import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
  type DesignAgentLog,
  type EvalCaseSummary,
  getWorkflowAnatomy,
  getWorkflowSkills,
  KernelApiError,
  listWorkflowEvalCases,
  type SkillSummary,
  type WorkflowAnatomy,
} from '@/lib/api'
import {
  EVAL_DIMENSIONS,
  EvalCasesSection,
  METRIC_DIMS,
  MetricSection,
  PrimitivesSection,
  SIM_DIMENSIONS,
  simulatorMeta,
  SimulatorSection,
  UI_DIMENSIONS,
} from '@/app/components/workflow-spec-sections'
import { GenerateEvalCasesButton } from '../../../[wfId]/eval-cases/generate-button'
import { ConfirmButton } from './confirm-button'
import { DesignAttribution } from './design-attribution'
import { ReviseButton } from './revise-button'
import { getTemplate } from '../../templates'

function templateNameFor(templateId: string): string {
  return getTemplate(templateId)?.name ?? templateId
}

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Step 2 of the new-workflow flow — review what NL-gen produced before
// the loop starts spending tokens against it. The kernel committed the
// row at the end of step 1 (spec + sim_plan + metric_definition are in
// DB), but eval cases haven't been generated yet (or were generated
// lazily by the iteration runner). This is the last chance to fix the
// description before the eval set crystallizes.
//
// Confirm → continue to the workflow detail page. Revise → delete the
// row and bounce back to /new (the kernel cascades skills + traces +
// audit; same DELETE the Settings tab uses).
//
// PLAN 8.4.11 — parity with mock `04-new-workflow-step2.html`. Four
// gen-section blocks plus the source-quote header carry the
// per-tool / per-persona / per-environment / metric provenance
// captions ("derived from <user phrase>" or "inferred from <pattern>")
// that prove NL-gen actually understood the description.
export default async function ReviewWorkflowPage({ params }: PageProps) {
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

  const continueHref = `/workspaces/${wsId}/workflows/${wfId}`
  const operateHref = `/workspaces/${wsId}/workflows/${wfId}/operate`
  const description = anatomy.description
  const spec = anatomy.spec
  const tools = spec.tools ?? []
  const personas = spec.environment?.personas ?? []
  const envGenerators = spec.environment?.env_generators ?? []
  const dataSources = spec.environment?.data_sources ?? []
  const primitives = spec.ui?.tabs?.[0]?.primitives ?? []
  const metricDef = anatomy.metric_definition ?? null
  const simPlan = anatomy.simulation_plan ?? null
  const designLog: DesignAgentLog | null = anatomy.design_agent_log ?? null

  // Aux skill count used in the meta line under the page header.
  const skillCount = skills.length
  const simMeta = simulatorMeta(
    tools,
    personas,
    envGenerators,
    dataSources,
    simPlan !== null,
  )

  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <h1 className="gen-title">Review what we&apos;ll build</h1>
        <p className="gen-sub">
          ownEvo generated the workflow spec, simulator, success metric,
          and (when available) the eval cases from your description.
          Look it over before the loop starts running. If anything is
          off, Revise deletes this row and lets you edit the
          description. Confirming opens the workflow and the next
          iteration writes against this spec.
        </p>
      </header>

      <Steps step="review" />

      <ReviewTabs wsId={wsId} wfId={wfId} active="review" />

      {description ? (
        <section className="source-quote">
          <div className="source-quote-label">From you</div>
          <div className="source-quote-col">
            <p className="source-quote-body">&ldquo;{description}&rdquo;</p>
            {anatomy.created_from_template ? (
              <div className="source-quote-template">
                Started from{' '}
                <strong>
                  {templateNameFor(anatomy.created_from_template)}
                </strong>{' '}
                template.
              </div>
            ) : null}
          </div>
        </section>
      ) : null}

      <SimulatorSection
        meta={simMeta}
        tools={tools}
        personas={personas}
        envGenerators={envGenerators}
        dataSources={dataSources}
        designLog={designLog}
        attributionSlot={
          <DesignAttribution log={designLog} dimensions={SIM_DIMENSIONS} />
        }
      />

      <EvalCasesSection
        cases={evalCases}
        wsId={wsId}
        wfId={wfId}
        emptyAction={
          <GenerateEvalCasesButton
            wsId={wsId}
            wfId={wfId}
            hasExisting={false}
          />
        }
        attributionSlot={
          <DesignAttribution log={designLog} dimensions={EVAL_DIMENSIONS} />
        }
      />

      <MetricSection
        metric={metricDef}
        attributionSlot={
          <DesignAttribution log={designLog} dimensions={METRIC_DIMS} />
        }
      />

      <PrimitivesSection
        primitives={primitives}
        operateHref={operateHref}
        skillCount={skillCount}
        attributionSlot={
          <DesignAttribution log={designLog} dimensions={UI_DIMENSIONS} />
        }
      />

      <div className="gen-action-row">
        <ReviseButton wsId={wsId} wfId={wfId} />
        <ConfirmButton continueHref={continueHref} />
      </div>
    </div>
  )
}

// Tabs between the parity review and the new Try-it surface. Two-tab
// nav lives just below the step indicator so the reviewer can dry-run
// a case without leaving the review flow.
function ReviewTabs({
  wsId,
  wfId,
  active,
}: {
  wsId: string
  wfId: string
  active: 'review' | 'try-it'
}) {
  return (
    <div className="tabs" style={{ marginBottom: 20 }}>
      <Link
        href={`/workspaces/${wsId}/workflows/new/review/${wfId}`}
        className={`tab${active === 'review' ? ' active' : ''}`}
      >
        Review generated
      </Link>
      <Link
        href={`/workspaces/${wsId}/workflows/new/review/${wfId}/try-it`}
        className={`tab${active === 'try-it' ? ' active' : ''}`}
      >
        Try it
      </Link>
    </div>
  )
}

// Inlined three-step indicator — same shape as the one on /new but
// scoped to this page so the review surface doesn't have to import
// from a sibling route's page component.
function Steps({ step }: { step: 'describe' | 'review' | 'baseline' }) {
  return (
    <div className="steps">
      <div className={`step done`}>
        <div className="step-num">✓</div>
        <div className="step-label">Describe</div>
      </div>
      <div className="step-connector" />
      <div className={`step ${step === 'review' ? 'active' : ''}`}>
        <div className="step-num">2</div>
        <div className="step-label">Review generated</div>
      </div>
      <div className="step-connector" />
      <div className={`step ${step === 'baseline' ? 'active' : ''}`}>
        <div className="step-num">3</div>
        <div className="step-label">Run baseline</div>
      </div>
    </div>
  )
}
