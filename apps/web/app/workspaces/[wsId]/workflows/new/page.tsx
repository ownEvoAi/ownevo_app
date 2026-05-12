import Link from 'next/link'
import { NewWorkflowForm } from './new-workflow-form'

interface PageProps {
  params: Promise<{ wsId: string }>
}

// Live NL-gen flow — describe a workflow, hit Generate, the kernel
// runs `generate_workflow_spec`, persists the row, and the form
// redirects to the workflow detail page.
export default async function NewWorkflowPage({ params }: PageProps) {
  const { wsId } = await params

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

      <Steps step="describe" />

      <NewWorkflowForm wsId={wsId} />

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
        <p style={{ marginTop: 12, fontSize: 12, color: 'var(--text-muted)' }}>
          Want a sample to start from? Run{' '}
          <code style={{ fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace" }}>
            make seed-demo
          </code>{' '}
          to seed credit-risk and contract-review, or browse the seeded
          workflows from the{' '}
          <Link href={`/workspaces/${wsId}`} style={{ color: 'var(--accent)' }}>
            Health page
          </Link>
          .
        </p>
      </div>
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
