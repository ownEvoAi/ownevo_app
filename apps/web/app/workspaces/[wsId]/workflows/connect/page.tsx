import Link from 'next/link'
import { EntryStrip } from '../new/page'
import { SourcePicker } from './source-picker'

interface PageProps {
  params: Promise<{ wsId: string }>
}

// Connect existing agent — step 1 of 3. Mock parity:
// www/preview/s26-rk7p3/24-existing-connect.html.
//
// The on-ramp: pick a trace source, then ownEvo infers the workflow
// from incoming traces and proposes improvements once it's seen
// enough activity (~100 traces in the marketing copy).
//
// Live wiring today:
//   * Manual description fallback — feeds the existing NL-gen flow.
//   * OTel endpoint + trace export upload — show the planned shape
//     and the operator-visible endpoint URL, but trace ingestion
//     itself is not yet wired. Picking either of those nudges the
//     operator to the Manual path with an explicit note.
//
// Wiring real ingestion is a sizeable kernel-side build (OTel
// receiver + workflow inference from gen-ai conventions + eval-case
// generation from sampled traces). Deferred until the on-ramp moves
// out of the planned-shape state.
export default async function ConnectExistingPage({ params }: PageProps) {
  const { wsId } = await params

  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <a
          href={`/workspaces/${wsId}/workflows/new`}
          className="wf-back"
          style={{ marginBottom: 6 }}
        >
          ‹ Back to new workflow
        </a>
        <h1 className="gen-title">Connect existing agent</h1>
        <p className="gen-sub">
          Hook an agent that&rsquo;s already running. ownEvo watches its
          traces, infers the workflow it&rsquo;s implementing, and proposes
          improvements once it has seen enough activity.
        </p>
      </header>

      <EntryStrip wsId={wsId} active="connect" />

      <ConnectSteps step={1} />

      <SourcePicker wsId={wsId} />
    </div>
  )
}

export function ConnectSteps({ step }: { step: 1 | 2 | 3 }) {
  const labels = ['Connect source', 'Inferred workflow', 'Generated eval set']
  return (
    <div className="connect-step-indicator">
      {labels.map((label, i) => {
        const idx = i + 1
        const state = idx < step ? 'done' : idx === step ? 'active' : ''
        return (
          <div key={label} style={{ display: 'flex', alignItems: 'center' }}>
            <div className={`connect-step ${state}`}>
              <span className="connect-step-num">
                {state === 'done' ? '✓' : idx}
              </span>
              <span>{label}</span>
            </div>
            {idx < labels.length ? (
              <div className="connect-step-sep" />
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
