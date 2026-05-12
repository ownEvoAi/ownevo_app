import { listPreviewWorkflows, type PreviewIndexEntry } from '@/lib/api'
import { NewWorkflowForm } from './new-workflow-form'

interface PageProps {
  params: Promise<{ wsId: string }>
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
export default async function NewWorkflowPage({ params }: PageProps) {
  const { wsId } = await params

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

      <Steps step="describe" />

      <NewWorkflowForm
        wsId={wsId}
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
