'use client'

import { useState, useTransition } from 'react'
import type { AgentStatus } from '@/lib/api'
import { updateAgentStatusAction } from './actions'

const STATUSES: AgentStatus[] = ['active', 'paused', 'archived']

interface Props {
 wsId: string
 agentId: string
 status: AgentStatus
}

// Inline status selector. Changing the value calls the server action and
// revalidates the registry page; an error rolls the select back and shows
// the kernel's message.
export function StatusControl({ wsId, agentId, status }: Props) {
 const [value, setValue] = useState<AgentStatus>(status)
 const [error, setError] = useState<string | null>(null)
 const [pending, startTransition] = useTransition function onChange(next: AgentStatus) {
 const previous = value
 setValue(next)
 setError(null)
 startTransition(async => {
 const result = await updateAgentStatusAction({ wsId, agentId, status: next })
 if (!result.ok) {
 setValue(previous)
 setError(result.error)
 }
 })
 }

 return (
 <span className="agent-status-control">
 <select
 className={`agent-status-select ${value}`}
 value={value}
 disabled={pending}
 aria-label="Agent status"
 onChange={(e) => onChange(e.target.value as AgentStatus)}
 // Row is itself a link; keep clicks on the select from navigating.
 onClick={(e) => e.stopPropagation }
 >
 {STATUSES.map((s) => (
 <option key={s} value={s}>
 {s}
 </option>
 ))}
 </select>
 {error && (
 <span role="alert" className="agent-status-error">
 {error}
 </span>
 )}
 </span>
 )
}
