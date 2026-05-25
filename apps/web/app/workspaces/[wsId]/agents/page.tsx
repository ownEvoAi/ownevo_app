import Link from 'next/link'
import { kernelError, listAgents, type Agent } from '@/lib/api'
import { relativeTime } from '@/lib/format'
import { StatusControl } from './status-control'

interface PageProps {
  params: Promise<{ wsId: string }>
}

const ORIGIN_LABEL: Record<Agent['origin'], string> = {
  greenfield: 'Greenfield',
  langsmith: 'LangSmith',
  copilot_studio: 'Copilot Studio',
}

// Agent registry — the workspace-wide index of every connected agent
// across origins (greenfield + imported). Each row deep-links to the
// workflow the agent runs; status is editable inline.
export default async function AgentsRegistryPage({ params }: PageProps) {
  const { wsId } = await params

  let agents: Agent[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    agents = (await listAgents()).items
  } catch (err) {
    apiError = kernelError(err)
  }

  return (
    <>
      <header className="page-header" style={{ marginBottom: 8 }}>
        <div>
          <h1 className="page-title">Agents</h1>
          <p className="page-subtitle">
            {agents.length} agent{agents.length === 1 ? '' : 's'} across every
            origin · each under a stable identity · workspace-scoped
          </p>
        </div>
      </header>

      {apiError && (
        <div role="alert" className="api-banner" style={{ marginBottom: 16 }}>
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      {agents.length === 0 && !apiError ? (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 24,
            color: 'var(--text-muted)',
            fontSize: 13,
            textAlign: 'center',
          }}
        >
          No agents registered yet. Creating a workflow or ingesting an
          imported agent&apos;s traces registers one here.
        </div>
      ) : (
        <div className="table-wrap">
          <div className="agent-row head">
            <div>Agent</div>
            <div>Origin</div>
            <div>Owner</div>
            <div>Iterations</div>
            <div>Eval cases</div>
            <div>Last iteration</div>
            <div>Status</div>
          </div>
          {agents.map((a) => (
            <div key={a.id} className="agent-row">
              <Link
                href={`/workspaces/${wsId}/workflows/${encodeURIComponent(a.workflow_id)}`}
                className="agent-name-cell"
                title={`identity ${a.identity_hash}`}
              >
                <span className="agent-name">{a.name}</span>
                <span className="agent-sub">{a.workflow_id}</span>
              </Link>
              <div>
                <span className={`agent-origin ${a.origin}`}>
                  {ORIGIN_LABEL[a.origin]}
                </span>
              </div>
              <div className="agent-metric">{a.owner ?? '—'}</div>
              <div className="agent-metric">{a.iteration_count}</div>
              <div className="agent-metric">{a.eval_coverage_count}</div>
              <div className="agent-metric">
                {a.last_iteration_at ? relativeTime(a.last_iteration_at) : '—'}
              </div>
              <div>
                <StatusControl wsId={wsId} agentId={a.id} status={a.status} />
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
