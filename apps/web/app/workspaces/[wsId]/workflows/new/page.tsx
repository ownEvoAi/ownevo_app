import Link from 'next/link'
import { gateStateFor, getDemoStatus } from '@/lib/demo-status'
import { NewWorkflowForm } from './new-workflow-form'
import { VERTICAL_TEMPLATES } from './templates'

interface PageProps {
 params: Promise<{ wsId: string }>
 searchParams: Promise<{ from?: string }>
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
 const demoGate = gateStateFor(await getDemoStatus())

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

 <EntryStrip wsId={wsId} active="greenfield" />

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

 <p className="journey-preview">
 What happens next: <strong>describe</strong> (~1 min) →
 <strong> review</strong> the generated spec (~10 s) →
 <strong> run iteration #1</strong> (~30-90 s) → failures cluster,
 the loop proposes an edit, you approve.
 </p>

 <NewWorkflowForm
 wsId={wsId}
 templates={VERTICAL_TEMPLATES}
 demoGate={demoGate}
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

// Dual entry point — mock parity with
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
 <Link
 href={
 active === 'greenfield'
 ? '#'
 : `/workspaces/${wsId}/workflows/new`
 }
 className={`entry-card${active === 'greenfield' ? ' active' : ''}`}
 style={{ textDecoration: 'none' }}
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
 </Link>
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
