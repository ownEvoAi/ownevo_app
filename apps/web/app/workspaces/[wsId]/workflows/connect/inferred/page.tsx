import Link from 'next/link'
import { EntryStrip } from '../../new/page'
import { ConnectSteps } from '../page'

interface PageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ source?: string }>
}

// Step 2 of 3 — Inferred workflow preview. For wired sources this
// would show the spec + ownership + cadence inferred from traces.
// Today only the manual-description path is wired (and that path
// bypasses this step), so this page is an explicit "planned shape,
// not yet implemented" surface. We keep the route + stepper visible
// so reviewers and operators see the planned on-ramp; the call-out
// makes the gap honest.
export default async function ConnectInferredPage({
  params,
  searchParams,
}: PageProps) {
  const { wsId } = await params
  const { source } = await searchParams
  const sourceLabel =
    source === 'otel'
      ? 'OpenTelemetry endpoint'
      : source === 'upload'
        ? 'Trace export upload'
        : 'Trace source'

  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <a
          href={`/workspaces/${wsId}/workflows/connect`}
          className="wf-back"
          style={{ marginBottom: 6 }}
        >
          ‹ Back: change source
        </a>
        <h1 className="gen-title">Inferred workflow</h1>
        <p className="gen-sub">
          ownEvo inspects ~100 traces from your agent and infers the
          workflow it&rsquo;s implementing — the steps, the reviewer, the
          decision boundaries.
        </p>
      </header>

      <EntryStrip wsId={wsId} active="connect" />
      <ConnectSteps step={2} />

      <div className="connect-not-wired">
        <div className="connect-not-wired-pill">Planned</div>
        <h2 className="connect-not-wired-title">
          {sourceLabel}: ingestion not yet wired
        </h2>
        <p className="connect-not-wired-body">
          The trace-ingestion + workflow-inference pipeline lands in a
          subsequent release. The shape on this page reflects what the
          inferred view will surface: the agent&rsquo;s steps (with frequency
          counts), inferred reviewer + cadence, and the top three patterns
          ownEvo would treat as anchors for the first eval cases.
        </p>
        <p className="connect-not-wired-body">
          For now the on-ramp that <strong>does</strong> work is the manual
          description path — it produces the same downstream artifacts
          (spec, simulation plan, eval cases, metric) using the NL-gen
          pipeline. Use that to spin up a workflow today; ingestion will
          attach to it later.
        </p>
        <div className="connect-not-wired-actions">
          <Link
            href={`/workspaces/${wsId}/workflows/new?from=connect`}
            className="btn btn-primary"
          >
            Continue with manual description →
          </Link>
          <Link
            href={`/workspaces/${wsId}/workflows/connect`}
            className="btn btn-secondary"
          >
            Pick a different source
          </Link>
        </div>
      </div>
    </div>
  )
}
