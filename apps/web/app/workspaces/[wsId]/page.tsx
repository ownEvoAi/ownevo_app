interface PageProps {
  params: Promise<{ wsId: string }>
}

// Workspace Health page placeholder. Slice 2 wires the real workflow-
// rows table + LiftChart against `GET /workflows` and
// `GET /workflows/{id}/iterations`.
export default async function WorkspaceHealthPage({ params }: PageProps) {
  const { wsId } = await params
  const wsLabel = wsId.charAt(0).toUpperCase() + wsId.slice(1)
  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Workflow health</h1>
          <p className="page-subtitle">{wsLabel} · workspace shell scaffolded · Health page wired in slice 2</p>
        </div>
      </header>
      <section style={{ padding: '24px 0', color: 'var(--text-muted)', fontSize: 13 }}>
        Workflow rows + lift chart land in W7 slice 2.
      </section>
    </>
  )
}
