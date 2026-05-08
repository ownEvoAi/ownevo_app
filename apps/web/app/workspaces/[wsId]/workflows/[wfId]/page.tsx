import { getMock } from './mocks'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

const ACTIVITY_TONE: Record<string, string> = {
  approval: 'var(--green)',
  cluster: 'var(--amber)',
  regression: 'var(--red)',
  escalation: 'var(--accent)',
}

// Workflow Overview tab.
//
// For wfId in {labour, contract, support}: renders mock metrics +
// recent-activity feed from mocks.ts. For demand-prediction (live):
// surfaces a placeholder pointing at Failures + Audit until the
// W8.1.1 wiring lands.
//
// The shape is intentionally identical between mock and live so
// swapping data sources is one read call away.
export default async function WorkflowOverviewPage({ params }: PageProps) {
  const { wsId, wfId } = await params
  const mock = getMock(wfId)

  if (mock) {
    return (
      <>
        <div className="metrics glance" style={{ marginBottom: 24 }}>
          {mock.metrics.map((m) => (
            <div key={m.label} className="metric">
              <div className="metric-label">{m.label}</div>
              <div className="metric-value">{m.value}</div>
              {m.delta && (
                <div className={`metric-delta ${m.delta.direction}`}>{m.delta.text}</div>
              )}
            </div>
          ))}
        </div>

        <section style={{ marginBottom: 24 }}>
          <h2
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: 'var(--text-2)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: 10,
            }}
          >
            Recent activity
          </h2>
          <div className="activity">
            {mock.recentActivity.map((a, i) => (
              <div key={i} className="activity-item">
                <div
                  className="activity-dot"
                  style={{ background: ACTIVITY_TONE[a.kind] ?? 'var(--text-faint)' }}
                />
                <div className="activity-body">
                  {a.body}
                  <div className="activity-meta">{a.when}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 20,
            color: 'var(--text-muted)',
            fontSize: 13,
            lineHeight: 1.55,
          }}
        >
          This workflow is positioning copy. The four glance metrics, the activity feed,
          and the failure clusters under the Failures tab are hand-authored. The
          improvement loop, eval-case promotion, gate, and audit chain are the same as
          for any live workflow — they just don&rsquo;t run on this dataset yet.
        </section>
      </>
    )
  }

  // Live workflow (demand-prediction or any other backend-registered id).
  return (
    <section
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 24,
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <h2 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8 }}>Overview</h2>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.55 }}>
        Live workflow Overview lands in W8.1.1 (workspace UI wired to the demand-
        prediction backend). For now use{' '}
        <a
          href={`/workspaces/${wsId}/workflows/${wfId}/failures`}
          style={{ color: 'var(--accent)' }}
        >
          Failures
        </a>{' '}
        for the cluster list and{' '}
        <a
          href={`/workspaces/${wsId}/workflows/${wfId}/audit`}
          style={{ color: 'var(--accent)' }}
        >
          Audit
        </a>{' '}
        for the chain — both read live from the kernel API.
      </p>
    </section>
  )
}
