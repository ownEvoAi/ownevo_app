'use client'

import { useState, useRef, useEffect } from 'react'
import Link from 'next/link'
import { workflowDisplayTitle } from '@/lib/format'
import type { WorkflowSummary } from '@/lib/api'

// Top-bar dropdown for switching workflows inside the operator shell.
// Mock parity: s26-rk7p3/28..31 each have a workflow chip in the top
// bar that hints at a dropdown ("▾"). This wires it.
export function WorkflowSwitcher({
  workflows,
  current,
  wsId,
  currentLabel,
}: {
  workflows: WorkflowSummary[]
  current: string
  wsId: string
  currentLabel: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  return (
    <div className="op-bar-workflow-wrap" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="op-bar-workflow"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <div>
          <div className="op-bar-workflow-name">{currentLabel}</div>
          <div className="op-bar-workflow-meta">{current}</div>
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>▾</span>
      </button>
      {open && (
        <div className="op-bar-menu" role="menu">
          {workflows.map((wf) => (
            <Link
              key={wf.id}
              href={`/operator/${wf.id}?ws=${encodeURIComponent(wsId)}`}
              className={`op-bar-menu-item${wf.id === current ? ' active' : ''}`}
              onClick={() => setOpen(false)}
            >
              <div className="op-bar-menu-name">
                {workflowDisplayTitle(wf.id, wf.description, 60)}
              </div>
              <div className="op-bar-menu-meta">
                {wf.id} · {wf.iteration_count} iter
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
