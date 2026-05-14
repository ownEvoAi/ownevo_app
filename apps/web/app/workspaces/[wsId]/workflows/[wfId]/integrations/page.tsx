// Mock parity: s26-rk7p3/13-workflow-integrations.html. Surface present;
// integration plumbing (LangFuse trace ingest, Slack approval routing,
// JIRA eval-case import) is downstream of the kernel growing those
// connectors.
import Link from 'next/link'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowIntegrationsPage({ params }: PageProps) {
  const { wsId, wfId } = await params
  return (
    <div className="planned-tab">
      <div className="planned-tab-pill">Planned</div>
      <h2 className="planned-tab-title">Integrations</h2>
      <p className="planned-tab-body">
        Connect this workflow to the systems it already talks to in
        production. The improvement loop reads from + writes to the
        same surface area the agent already runs against.
      </p>
      <ul className="planned-tab-list">
        <li>
          <strong>LangFuse / Phoenix / OTel trace ingest</strong> —
          stream live agent traces so the iteration runner can score
          production runs against the regression gate, not just
          replays. Pairs with the Connect-existing-agent on-ramp.
        </li>
        <li>
          <strong>Slack approval routing</strong> — pending proposals
          drop into a Slack channel; threaded approvals decide.
        </li>
        <li>
          <strong>JIRA / Linear eval-case import</strong> — import bug
          tickets describing a past miss; ownEvo generates the matching
          eval case from the ticket body.
        </li>
      </ul>
      <p className="planned-tab-body">
        Per-integration kernel work is non-trivial (each needs its own
        receiver + auth flow + retry semantics); these light up
        sequentially as customers ask for them. The
        Connect-existing-agent on-ramp covers the trace ingest piece.
      </p>
      <div className="planned-tab-actions">
        <Link
          href={`/workspaces/${wsId}/workflows/connect`}
          className="btn btn-secondary"
        >
          Open the BYO-agent on-ramp →
        </Link>
      </div>
    </div>
  )
}
