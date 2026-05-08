interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

// Overview tab — placeholder until slice 6 fills out the workflow
// detail page (mock: 05-workflow-overview.html). For W7 the visible
// surfaces are Failures (slice 3) and Audit (slice 4) — both linked
// from the tab strip in the parent layout.
export default async function WorkflowOverviewPage({ params }: PageProps) {
  const { wsId, wfId } = await params
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
        Workflow Overview lands in slice 6. Until then, jump to{' '}
        <a
          href={`/workspaces/${wsId}/workflows/${wfId}/failures`}
          style={{ color: 'var(--accent)' }}
        >
          Failures
        </a>{' '}
        or{' '}
        <a
          href={`/workspaces/${wsId}/workflows/${wfId}/audit`}
          style={{ color: 'var(--accent)' }}
        >
          Audit
        </a>{' '}
        to see live data for this workflow.
      </p>
    </section>
  )
}
