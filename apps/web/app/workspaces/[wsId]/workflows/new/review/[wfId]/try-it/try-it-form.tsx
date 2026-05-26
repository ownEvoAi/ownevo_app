'use client'

import { useState, useTransition } from 'react'
import {
 type EvalCaseSummary,
 KernelApiError,
 type TryItResponse,
 tryWorkflow,
} from '@/lib/api'

interface Props {
 wfId: string
 cases: EvalCaseSummary[]
}

/**
 * Try-it surface. Reviewer picks one generated eval case,
 * clicks Run, sees the agent execute end-to-end with structured output
 * + trace + cost. Zero writes to iterations/proposals/audit — backed
 * by POST /api/workflows/{id}/try.
 *
 * Three-panel layout (left: input, centre: trace, right: output) mirrors
 * On smaller viewports the panels
 * stack vertically (CSS grid handles the reflow).
 */
export function TryItForm({ wfId, cases }: Props) {
 const [selectedId, setSelectedId] = useState<string>(cases[0]?.id ?? '')
 const [result, setResult] = useState<TryItResponse | null>(null)
 const [error, setError] = useState<string | null>(null)
 const [pending, startTransition] = useTransition()
 const selected = cases.find((c) => c.id === selectedId) ?? null

 function onRun() {
 if (!selectedId) return
 setError(null)
 setResult(null)
 startTransition(async () => {
 try {
 const res = await tryWorkflow(wfId, { eval_case_id: selectedId })
 setResult(res)
 } catch (err) {
 const msg =
 err instanceof KernelApiError
 ? err.message
 : err instanceof Error
 ? err.message
 : String(err)
 setError(msg)
 }
 })
 }

 if (cases.length === 0) {
 return (
 <div className="try-it-empty">
 <p>
 No eval cases generated yet. Open <strong>Review generated</strong>{' '}
 and click <em>Generate now</em> to seed the suite — Try-it picks one
 case from the generated set.
 </p>
 </div>
 )
 }

 return (
 <div className="try-it-wrap">
 <div className="try-it-picker">
 <label htmlFor="try-it-case" className="try-it-picker-label">
 Eval case
 </label>
 <select
 id="try-it-case"
 className="try-it-picker-select"
 value={selectedId}
 onChange={(e) => setSelectedId(e.target.value)}
 disabled={pending}
 >
 {cases.map((c, i) => (
 <option key={c.id} value={c.id}>
 {i + 1}. {c.case_id} ({c.is_test_fold ? 'test' : 'train'})
 </option>
 ))}
 </select>
 <button
 type="button"
 className="btn btn-primary"
 onClick={onRun}
 disabled={pending || !selectedId}
 >
 {pending ? 'Running…' : 'Run case ›'}
 </button>
 </div>

 {error ? (
 <div role="alert" className="api-banner">
 <strong>Try-it failed.</strong> {error}
 </div>
 ) : null}

 <div className="try-it-grid">
 {/* Input panel */}
 <section className="try-it-panel">
 <h3 className="try-it-panel-title">Input</h3>
 {selected ? (
 <dl className="try-it-input-dl">
 <dt>case_id</dt>
 <dd>
 <code>{selected.case_id}</code>
 </dd>
 <dt>target_label_field</dt>
 <dd>
 <code>{selected.target_label_field ?? '—'}</code>
 </dd>
 <dt>expected_value</dt>
 <dd>
 <code>{String(selected.expected_value)}</code>
 </dd>
 <dt>fold</dt>
 <dd>
 <span
 className={`pill ${selected.is_test_fold ? 'accent' : 'outline'}`}
 >
 {selected.is_test_fold ? 'test' : 'train'}
 </span>
 </dd>
 {selected.expected_behavior_provenance ? (
 <>
 <dt>source</dt>
 <dd className="try-it-source">
 {selected.expected_behavior_provenance.kind === 'derived' ? (
 <>
 From: <em>&ldquo;{selected.expected_behavior_provenance.source}&rdquo;</em>
 </>
 ) : (
 <>
 Pattern: <em>{selected.expected_behavior_provenance.source}</em>
 </>
 )}
 </dd>
 </>
 ) : null}
 {selected.rationale ? (
 <>
 <dt>rationale</dt>
 <dd>{selected.rationale}</dd>
 </>
 ) : null}
 </dl>
 ) : (
 <p className="try-it-empty-inline">Pick an eval case to start.</p>
 )}
 </section>

 {/* Trace panel */}
 <section className="try-it-panel">
 <h3 className="try-it-panel-title">Trace</h3>
 {result ? (
 <ol className="try-it-trace-list">
 {result.trace.map((evt) => (
 <li key={evt.event_id} className="try-it-trace-evt">
 <div className="try-it-trace-row">
 <span
 className={`pill ${evt.status === 'error' ? 'red' : 'accent'}`}
 >
 {evt.type.replace(/_/g, ' ')}
 </span>
 {evt.duration_ms != null ? (
 <span className="try-it-trace-meta">
 {evt.duration_ms} ms
 </span>
 ) : null}
 </div>
 {evt.name ? (
 <code className="try-it-trace-name">{evt.name}</code>
 ) : null}
 {evt.error ? (
 <div className="try-it-trace-error">
 {evt.error_class ? <strong>{evt.error_class}:</strong> : null}{' '}
 {evt.error}
 </div>
 ) : null}
 </li>
 ))}
 </ol>
 ) : pending ? (
 <p className="try-it-empty-inline">Running the agent…</p>
 ) : (
 <p className="try-it-empty-inline">
 Trace appears here after you run a case.
 </p>
 )}
 </section>

 {/* Output panel */}
 <section className="try-it-panel">
 <h3 className="try-it-panel-title">Output</h3>
 {result ? (
 <>
 <div className="try-it-result-header">
 <span
 className={`pill ${result.passed ? 'green' : 'red'}`}
 style={{ fontSize: 12 }}
 >
 {result.passed ? 'PASS' : 'FAIL'}
 </span>
 <span className="try-it-result-meta">
 {result.duration_ms} ms
 </span>
 <span className="try-it-result-meta">
 {result.cost_usd > 0
 ? `$${result.cost_usd.toFixed(4)}`
 : 'cost —'}
 </span>
 </div>
 <dl className="try-it-input-dl">
 <dt>predicted</dt>
 <dd>
 <code>{String(result.actual_value ?? '—')}</code>
 </dd>
 <dt>expected</dt>
 <dd>
 <code>{String(result.expected_value)}</code>
 </dd>
 <dt>model</dt>
 <dd>
 <code>{result.model}</code>
 </dd>
 <dt>tokens</dt>
 <dd>
 <code>
 {result.input_tokens} in / {result.output_tokens} out
 </code>
 </dd>
 </dl>
 {result.rationale ? (
 <div className="try-it-rationale">
 <strong>Rationale:</strong> {result.rationale}
 </div>
 ) : null}
 </>
 ) : pending ? (
 <p className="try-it-empty-inline">Waiting for agent response…</p>
 ) : (
 <p className="try-it-empty-inline">
 Output, pass/fail, cost, and rationale appear here.
 </p>
 )}
 </section>
 </div>
 </div>
 )
}
