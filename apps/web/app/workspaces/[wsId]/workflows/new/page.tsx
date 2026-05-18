import Link from 'next/link'
import { listPreviewWorkflows, type PreviewIndexEntry } from '@/lib/api'
import { NewWorkflowForm } from './new-workflow-form'
import { VERTICAL_TEMPLATES } from './templates'

interface PageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ from?: string }>
}

// Friendly labels for the sample-fill chips. The kernel emits the
// fixture ids verbatim; this map gives them a more readable name in
// the UI without coupling the kernel to display copy.
const SAMPLE_LABEL: Record<string, string> = {
  'demand-prediction': 'Demand prediction',
  'credit-risk': 'Credit risk',
  'contract-review': 'Contract review',
}

// Live NL-gen flow — describe a workflow, hit Generate, the kernel
// runs `generate_workflow_spec`, persists the row, and the form
// redirects to the workflow detail page.
export default async function NewWorkflowPage({
  params,
  searchParams,
}: PageProps) {
  const { wsId } = await params
  const { from } = await searchParams
  const fromConnect = from === 'connect'

  // Sample descriptions come from the kernel's NL-gen fixtures.
  // Surfacing them here lets reviewers click-to-fill the textarea
  // instead of typing a description from scratch.
  let samples: PreviewIndexEntry[] = []
  try {
    samples = (await listPreviewWorkflows()).items
  } catch {
    // Form still works without samples — the kernel preview endpoint
    // is read-only and doesn't gate the live gen path.
  }

  return (
    <div className="preview-wrap">
      <header className="gen-head">
        <h1 className="gen-title">New workflow</h1>
        <p className="gen-sub">
          Describe the workflow in plain English &mdash; what the agent does,
          what data it reads, who reviews its output, and what past failures
          you want it to avoid. ownEvo generates a workflow spec from your
          description, then the improvement loop takes over.
        </p>
      </header>

      <EntryStrip wsId={wsId} active={fromConnect ? 'connect' : 'greenfield'} />

      {fromConnect ? (
        <div className="connect-context-note">
          You picked <strong>Manual description</strong> on the Connect
          on-ramp. Describe your existing agent below; ownEvo generates
          the spec + eval set from your description.{' '}
          <Link
            href={`/workspaces/${wsId}/workflows/connect`}
            style={{ color: 'var(--accent)' }}
          >
            Change source ↩
          </Link>
        </div>
      ) : null}

      <Steps step="describe" />

      <NewWorkflowForm
        wsId={wsId}
        templates={VERTICAL_TEMPLATES}
        samples={samples.map((s) => ({
          id: s.workflow_id,
          label: SAMPLE_LABEL[s.workflow_id] ?? s.workflow_id,
          description: s.description,
        }))}
      />

      <div className="gen-help">
        <h3 className="gen-help-title">What makes a good description?</h3>
        <ul className="gen-help-list">
          <li>
            <strong>Outcome:</strong> what the agent decides or produces, in
            one sentence.
          </li>
          <li>
            <strong>Inputs:</strong> what data sources or signals the agent
            reads.
          </li>
          <li>
            <strong>Reviewer:</strong> the human (role + cadence) who reviews
            output, and what makes a decision &ldquo;correct.&rdquo;
          </li>
          <li>
            <strong>Past misses:</strong> two or three specific failures you
            want the loop to learn from.
          </li>
        </ul>
      </div>
    </div>
  )
}

// Dual entry point — mock parity with www/preview/s26-rk7p3/24.
// "Greenfield" routes to /workflows/new (the NL-gen flow on this page).
// "Connect existing" routes to /workflows/connect — a 3-step wizard
// for hooking up an existing agent's traces. Only the manual-description
// path on the wizard is functional today; OTel + upload show their
// planned shape but aren't yet wired through to ingestion.
export function EntryStrip({
  wsId,
  active,
}: {
  wsId: string
  active: 'greenfield' | 'connect'
}) {
  return (
    <div className="entry-strip">
      <div
        className={`entry-card${active === 'greenfield' ? ' active' : ' dim'}`}
      >
        <div className="entry-icon" aria-hidden>
          <svg viewBox="0 0 16 16">
            <path d="M8 3 L8 13 M3 8 L13 8" />
          </svg>
        </div>
        <div>
          <div className="entry-title">
            Greenfield: design from scratch
            {active === 'greenfield' ? ' ← you are here' : ''}
          </div>
          <div className="entry-meta">
            Describe the workflow, ownEvo generates a baseline spec, you
            run the first iteration.
          </div>
        </div>
      </div>
      <Link
        href={
          active === 'connect'
            ? '#'
            : `/workspaces/${wsId}/workflows/connect`
        }
        className={`entry-card${active === 'connect' ? ' active' : ''}`}
        style={{ textDecoration: 'none' }}
      >
        <div className="entry-icon" aria-hidden>
          <svg viewBox="0 0 16 16">
            <path d="M3 11 L6 6 L9 9 L13 4 M9 4 L13 4 L13 8" />
          </svg>
        </div>
        <div>
          <div className="entry-title">
            Connect existing agent
            {active === 'connect' ? ' ← you are here' : ''}
          </div>
          <div className="entry-meta">
            Hook an agent that&rsquo;s already running, infer the workflow
            from its traces, propose improvements.
          </div>
        </div>
      </Link>
    </div>
  )
}

function Steps({ step }: { step: 'describe' | 'review' | 'baseline' }) {
  return (
    <div className="steps">
      <div className={`step ${step === 'describe' ? 'active' : 'done'}`}>
        <div className="step-num">{step === 'describe' ? '1' : '✓'}</div>
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
