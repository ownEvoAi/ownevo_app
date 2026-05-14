// Mock parity: s26-rk7p3/12-workflow-triggers.html. Surface present;
// trigger scheduling (cron + webhook ingest + manual run buttons) is
// downstream of the kernel growing an explicit Triggers domain. We
// surface the planned shape and a working "Run iteration" affordance
// that already lives on the Overview.
import Link from 'next/link'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowTriggersPage({ params }: PageProps) {
  const { wsId, wfId } = await params
  return (
    <div className="planned-tab">
      <div className="planned-tab-pill">Planned</div>
      <h2 className="planned-tab-title">Triggers</h2>
      <p className="planned-tab-body">
        Define what kicks off an iteration. The kernel today supports
        one trigger — the operator clicking <em>Run iteration</em> on the
        Overview tab. The planned shape adds:
      </p>
      <ul className="planned-tab-list">
        <li>
          <strong>Cron schedules</strong> — recurring iterations (e.g.
          weekly Monday 09:00) that run unattended; results land in the
          inbox for review.
        </li>
        <li>
          <strong>Webhook ingest</strong> — kick off an iteration when an
          upstream system (Slack, PagerDuty, your CI) signals a new
          batch is ready.
        </li>
        <li>
          <strong>Cluster-driven</strong> — a new failure cluster of
          severity ≥ medium triggers an iteration targeted at that
          cluster automatically.
        </li>
      </ul>
      <p className="planned-tab-body">
        The supporting kernel work (a <code>triggers</code> table + a
        scheduler reading it + a webhook receiver) lands when the first
        customer asks for unattended cadence; for now, manual is the
        only path.
      </p>
      <div className="planned-tab-actions">
        <Link
          href={`/workspaces/${wsId}/workflows/${wfId}`}
          className="btn btn-primary"
        >
          Run iteration on Overview →
        </Link>
      </div>
    </div>
  )
}
