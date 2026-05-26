'use client'

import { useRef, useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import type { DataUpload } from '@/lib/api'
import { deleteUploadAction, uploadFileAction } from './actions'

function summarize(u: DataUpload): string {
 if (u.row_count != null) {
 const cols = (u.schema?.columns as { name: string }[] | undefined)?.length ?? 0
 return `${u.row_count} rows · ${cols} columns`
 }
 const pages = u.schema?.page_count ?? u.schema?.paragraph_count
 const tables = u.schema?.table_count ?? 0
 return [pages != null ? `${pages} pages/paras` : null, `${tables} tables`]
 .filter(Boolean)
 .join(' · ')
}

// Client island for the uploads page: pick a file, upload it (parsed
// server-side by the kernel), and manage the parsed uploads.
export function UploadForm({
 wsId,
 uploads,
 demoMode = false,
}: {
 wsId: string
 uploads: DataUpload[]
 demoMode?: boolean
}) {
 const router = useRouter()
 const [isPending, startTransition] = useTransition()
 const [error, setError] = useState<string | null>(null)
 const inputRef = useRef<HTMLInputElement>(null)

 function upload(formData: FormData) {
 setError(null)
 startTransition(async () => {
 const r = await uploadFileAction(wsId, formData)
 if (!r.ok) return setError(r.error)
 if (inputRef.current) inputRef.current.value = ''
 router.refresh })
 }

 function remove(id: string) {
 setError(null)
 startTransition(async () => {
 const r = await deleteUploadAction(wsId, id)
 if (!r.ok) return setError(r.error)
 router.refresh })
 }

 return (
 <div className="settings-stack">
 <div className="settings-card">
 <div className="settings-card-header">
 <h2 className="settings-card-title">Upload a file</h2>
 <p className="settings-card-subtitle">
 CSV, Excel, or Parquet spreadsheets and PDF / DOCX documents. ownEvo
 parses the file once; the workflow&apos;s agent reads it on every
 iteration without re-upload.
 </p>
 </div>
 <form action={upload}>
 <input
 ref={inputRef}
 type="file"
 name="file"
 accept=".csv,.xlsx,.xls,.parquet,.pdf,.docx"
 disabled={isPending || demoMode}
 style={{ fontSize: 13 }}
 />
 <div style={{ marginTop: 12 }}>
 <button
 type="submit"
 disabled={isPending || demoMode}
 className="btn btn-primary"
 >
 {isPending ? 'Uploading…' : 'Upload'}
 </button>
 </div>
 </form>
 </div>

 <div className="settings-card">
 <div className="settings-card-header">
 <h2 className="settings-card-title">Uploaded files</h2>
 </div>
 {uploads.length === 0 ? (
 <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
 No files uploaded yet.
 </p>
 ) : (
 <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
 {uploads.map((u) => (
 <li
 key={u.id}
 style={{
 display: 'flex',
 alignItems: 'center',
 justifyContent: 'space-between',
 gap: 8,
 padding: '8px 0',
 borderTop: '1px solid var(--border)',
 flexWrap: 'wrap',
 }}
 >
 <span style={{ fontSize: 13 }}>
 <strong>{u.name}</strong>{' '}
 <span style={{ color: 'var(--text-muted)' }}>
 · {u.kind} · {summarize(u)}
 </span>
 </span>
 <button
 type="button"
 onClick={() => remove(u.id)}
 disabled={isPending || demoMode}
 className="btn btn-danger"
 >
 Remove
 </button>
 </li>
 ))}
 </ul>
 )}
 </div>

 {error && (
 <p role="alert" style={{ fontSize: 12.5, color: 'var(--danger, #c0392b)' }}>
 {error}
 </p>
 )}
 </div>
 )
}
