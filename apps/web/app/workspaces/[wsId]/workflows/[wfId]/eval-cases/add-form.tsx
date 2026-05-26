'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { addEvalCaseAction } from './lifecycle-actions'

// Client island for manual eval-case add. Hand-authored cases land
// with provenance='hand-authored' on the kernel side.
export function AddEvalCaseForm({
 wsId,
 wfId,
 defaultTargetLabel,
}: {
 wsId: string
 wfId: string
 defaultTargetLabel: string
}) {
 const router = useRouter const [open, setOpen] = useState(false)
 const [isPending, startTransition] = useTransition const [caseId, setCaseId] = useState('')
 const [expected, setExpected] = useState<'true' | 'false'>('true')
 const [targetField, setTargetField] = useState(defaultTargetLabel || 'label')
 const [rationale, setRationale] = useState('')
 const [isTestFold, setIsTestFold] = useState(false)
 const [error, setError] = useState<string | null>(null)

 function submit {
 setError(null)
 startTransition(async => {
 const result = await addEvalCaseAction({
 wsId,
 wfId,
 payload: {
 case_id: caseId.trim ,
 expected_value: expected === 'true',
 target_label_field: targetField.trim ,
 rationale: rationale.trim || undefined,
 is_test_fold: isTestFold,
 },
 })
 if (!result.ok) {
 setError(result.error)
 return
 }
 setOpen(false)
 setCaseId('')
 setRationale('')
 router.refresh })
 }

 if (!open) {
 return (
 <button
 type="button"
 onClick={ => setOpen(true)}
 className="btn btn-secondary"
 style={{ fontSize: 12, padding: '6px 12px' }}
 >
 + Add case manually
 </button>
 )
 }

 return (
 <div className="eval-add-card">
 <div className="eval-add-header">Add eval case</div>
 <div className="eval-add-grid">
 <label>
 <span>case_id</span>
 <input
 type="text"
 value={caseId}
 onChange={(e) => setCaseId(e.target.value)}
 placeholder="e.g. high-utilization-borderline"
 disabled={isPending}
 />
 </label>
 <label>
 <span>target_label_field</span>
 <input
 type="text"
 value={targetField}
 onChange={(e) => setTargetField(e.target.value)}
 disabled={isPending}
 />
 </label>
 <label>
 <span>expected_value</span>
 <select
 value={expected}
 onChange={(e) => setExpected(e.target.value as 'true' | 'false')}
 disabled={isPending}
 >
 <option value="true">true</option>
 <option value="false">false</option>
 </select>
 </label>
 <label className="eval-add-fold">
 <input
 type="checkbox"
 checked={isTestFold}
 onChange={(e) => setIsTestFold(e.target.checked)}
 disabled={isPending}
 />
 <span>test fold</span>
 </label>
 </div>
 <label className="eval-add-rationale">
 <span>rationale (optional)</span>
 <input
 type="text"
 value={rationale}
 onChange={(e) => setRationale(e.target.value)}
 placeholder="One-line note on why this case is in the suite"
 disabled={isPending}
 />
 </label>

 <div className="eval-add-actions">
 <button
 type="button"
 onClick={submit}
 disabled={isPending || !caseId.trim || !targetField.trim }
 className="btn btn-primary"
 style={{ fontSize: 12, padding: '6px 14px' }}
 >
 {isPending ? 'Adding…' : 'Add case'}
 </button>
 <button
 type="button"
 onClick={ => {
 setOpen(false)
 setError(null)
 }}
 disabled={isPending}
 className="btn btn-secondary"
 style={{ fontSize: 12, padding: '6px 12px' }}
 >
 Cancel
 </button>
 </div>

 {error ? (
 <p role="alert" className="settings-error" style={{ marginTop: 8 }}>
 {error}
 </p>
 ) : null}
 </div>
 )
}
