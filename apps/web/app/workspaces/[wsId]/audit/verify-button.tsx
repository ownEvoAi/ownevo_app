'use client'

import { useState, useTransition } from 'react'
import { verifyAuditChainAction, type VerifyState } from './actions'

interface VerifyButtonProps {
 wsId: string
}

// Client island — wraps the verify Server Action in a useTransition() +
// inline-result panel so the page doesn't navigate away on click.
//
// The panel renders both the happy path (valid chain) and the gap /
// duplicate diagnostic (invalid chain). API errors render as a
// non-blocking alert.
export function VerifyButton({ wsId }: VerifyButtonProps) {
 const [pending, startTransition] = useTransition()
 const [state, setState] = useState<VerifyState | null>(null)

 const onClick = () => {
 startTransition(async () => {
 const result = await verifyAuditChainAction(wsId)
 setState(result)
 })
 }

 return (
 <div>
 <button
 type="button"
 className="btn btn-secondary"
 onClick={onClick}
 disabled={pending}
 >
 {pending ? 'Verifying…' : 'Verify chain'}
 </button>

 {state?.error && (
 <div role="alert" className="api-banner" style={{ marginTop: 12 }}>
 <strong>Verify failed.</strong> {state.error}
 </div>
 )}

 {state?.result && (
 <div
 style={{
 marginTop: 12,
 padding: '12px 14px',
 border: `1px solid ${state.result.valid ? 'var(--green)' : 'var(--red)'}`,
 background: state.result.valid ? 'var(--green-soft)' : 'var(--red-soft)',
 color: 'var(--text)',
 borderRadius: 6,
 fontSize: 12.5,
 }}
 >
 <div style={{ fontWeight: 500, marginBottom: 4 }}>
 {state.result.valid ? '✓ Chain is intact' : '✗ Chain integrity issue'}
 </div>
 <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
 {state.result.total_entries} entries · seq{' '}
 {state.result.min_seq ?? '—'}–{state.result.max_seq ?? '—'} ·{' '}
 canonical export {state.result.canonical_export_bytes.toLocaleString()} bytes
 </div>
 {state.result.missing_seqs.length > 0 && (
 <div style={{ fontSize: 11.5, color: 'var(--red)', marginTop: 4 }}>
 Missing seqs: {state.result.missing_seqs.slice(0, 8).join(', ')}
 {state.result.missing_seqs.length > 8 ? ' …' : ''}
 </div>
 )}
 {state.result.duplicate_seqs.length > 0 && (
 <div style={{ fontSize: 11.5, color: 'var(--red)', marginTop: 4 }}>
 Duplicate seqs: {state.result.duplicate_seqs.slice(0, 8).join(', ')}
 {state.result.duplicate_seqs.length > 8 ? ' …' : ''}
 </div>
 )}
 </div>
 )}
 </div>
 )
}
