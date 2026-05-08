import Link from 'next/link'
import type {
  AgentToolSpec,
  ReviewerSpec,
  SkillSummary,
  WorkflowSpecShape,
} from '@/lib/api'

// W7 slice 11 (7.1.12) — "what the agent CAN do" pane.
//
// Three-column anatomy: skills active · tools available · topology +
// reviewer. Mock parity: section in 05-workflow-overview.html. Reads
// from substrate skills (passed in) + workflow.spec (also passed in)
// so the component is pure-render — both live and mock surfaces feed
// it the same shape.
//
// Topology is fixed at "single-agent loop" for MVP; D4 / Phase-2
// multi-agent topology is explicitly out-of-scope for the W7 demo
// (PLAN.md § "NOT in MVP" — multi-agent topology graph view).

interface Props {
  wsId: string
  workflowId: string | null
  skills: SkillSummary[]
  spec: WorkflowSpecShape | null
}

export function AgentAnatomy({ wsId, workflowId, skills, spec }: Props) {
  const tools = spec?.tools ?? []
  const reviewer = spec?.reviewer
  const successCriterion = spec?.success_criterion
  const envSummary = summarizeEnvironment(spec?.environment)

  return (
    <section className="anatomy">
      <h2 className="section-title">Agent anatomy</h2>
      <p className="anatomy-lede">
        Single-agent loop, gated by the regression suite. Below: the
        skills the agent has loaded, the tools it can call, and who
        signs off on changes.
      </p>

      <div className="anatomy-grid">
        <SkillsColumn wsId={wsId} skills={skills} />
        <ToolsColumn tools={tools} />
        <TopologyColumn
          reviewer={reviewer}
          successCriterion={successCriterion}
          environmentSummary={envSummary}
        />
      </div>

      {workflowId && (
        <p className="anatomy-footnote">
          Skills + tools are read live from the kernel.{' '}
          <Link href={`/workspaces/${wsId}/workflows/${workflowId}/traces`}>
            Open the trace inspector
          </Link>{' '}
          to watch one run end-to-end.
        </p>
      )}
    </section>
  )
}

function SkillsColumn({
  wsId,
  skills,
}: {
  wsId: string
  skills: SkillSummary[]
}) {
  return (
    <div className="anatomy-col">
      <div className="anatomy-col-head">Skills active · {skills.length}</div>
      {skills.length === 0 ? (
        <div className="anatomy-empty">
          No skills bound to this workflow yet — generated on first run.
        </div>
      ) : (
        <ul className="anatomy-list">
          {skills.map((s) => (
            <li key={s.id} className="anatomy-row">
              <Link
                href={`/workspaces/${wsId}/skills/${encodeURIComponent(s.id)}`}
                className="anatomy-row-name"
              >
                {s.id}
              </Link>
              <div className="anatomy-row-meta">
                <span className="pill outline">{s.kind}</span>
                {s.head_version_seq !== null && (
                  <span className="pill outline">v{s.head_version_seq}</span>
                )}
                {s.capability_tags.slice(0, 3).map((t) => (
                  <span key={t} className="anatomy-tag">
                    {t}
                  </span>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ToolsColumn({ tools }: { tools: AgentToolSpec[] }) {
  return (
    <div className="anatomy-col">
      <div className="anatomy-col-head">Tools available · {tools.length}</div>
      {tools.length === 0 ? (
        <div className="anatomy-empty">
          No tools listed in the workflow spec — NL-gen has not produced one yet.
        </div>
      ) : (
        <ul className="anatomy-list">
          {tools.map((t) => (
            <li key={t.name} className="anatomy-row">
              <span className="anatomy-row-name">{t.name}</span>
              {t.description && (
                <div className="anatomy-row-desc">{t.description}</div>
              )}
              <code className="anatomy-row-sig">{toolSignature(t)}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TopologyColumn({
  reviewer,
  successCriterion,
  environmentSummary,
}: {
  reviewer: ReviewerSpec | undefined
  successCriterion: WorkflowSpecShape['success_criterion']
  environmentSummary: string | null
}) {
  return (
    <div className="anatomy-col">
      <div className="anatomy-col-head">Topology &amp; review</div>
      <ul className="anatomy-list">
        <li className="anatomy-row">
          <span className="anatomy-row-name">Single-agent loop</span>
          <div className="anatomy-row-desc">
            One agent reads its skills, calls tools, and proposes the next
            skill version. Regression gate runs every iteration. Phase-2
            multi-agent is out of scope.
          </div>
        </li>
        {reviewer && (
          <li className="anatomy-row">
            <span className="anatomy-row-name">Reviewer · {reviewer.role}</span>
            {reviewer.cadence && (
              <div className="anatomy-row-desc">cadence: {reviewer.cadence}</div>
            )}
            {reviewer.description && (
              <div className="anatomy-row-desc">{reviewer.description}</div>
            )}
          </li>
        )}
        {successCriterion?.target_metric_name && (
          <li className="anatomy-row">
            <span className="anatomy-row-name">
              Success · {successCriterion.direction ?? 'maximize'}{' '}
              {successCriterion.target_metric_name}
            </span>
            {successCriterion.description && (
              <div className="anatomy-row-desc">{successCriterion.description}</div>
            )}
          </li>
        )}
        {environmentSummary && (
          <li className="anatomy-row">
            <span className="anatomy-row-name">Environment</span>
            <div className="anatomy-row-desc">{environmentSummary}</div>
          </li>
        )}
      </ul>
    </div>
  )
}

function toolSignature(tool: AgentToolSpec): string {
  const inputs =
    tool.inputs && tool.inputs.length > 0
      ? tool.inputs
          .map((p) => `${p.name}: ${p.type}${p.required === false ? '?' : ''}`)
          .join(', ')
      : ''
  const outputs =
    tool.outputs && tool.outputs.length > 0
      ? tool.outputs.map((p) => `${p.name}: ${p.type}`).join(', ')
      : ''
  if (outputs) return `${tool.name}(${inputs}) → ${outputs}`
  return `${tool.name}(${inputs})`
}

function summarizeEnvironment(
  env: WorkflowSpecShape['environment'],
): string | null {
  if (!env) return null
  const parts: string[] = []
  if (env.entities?.length) parts.push(`${env.entities.length} entity types`)
  if (env.data_sources?.length)
    parts.push(`${env.data_sources.length} data sources`)
  if (env.env_generators?.length)
    parts.push(`${env.env_generators.length} generators`)
  if (env.personas?.length) parts.push(`${env.personas.length} personas`)
  if (env.seasonality?.length) parts.push(`seasonality: ${env.seasonality.join(', ')}`)
  return parts.length > 0 ? parts.join(' · ') : null
}
