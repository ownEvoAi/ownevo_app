'use client'

import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'
import { workflowDisplayTitle, workspaceLabel } from '../../../lib/format'
import type { WorkflowSummary } from '../../../lib/api'

interface NavProps {
  wsId: string
  workflows: WorkflowSummary[]
  themeToggle: ReactNode
}

// Sidebar for /workspaces/[wsId]/... routes. Active-item highlight
// derived from the current pathname; workflow list comes from the
// kernel (passed in from the workspace layout server component).
export function WorkspaceNav({ wsId, workflows, themeToggle }: NavProps) {
  const pathname = usePathname() ?? ''
  const root = `/workspaces/${wsId}`

  const isActive = (href: string) => {
    if (href === root) return pathname === root
    return pathname === href || pathname.startsWith(`${href}/`)
  }
  const cls = (href: string) => `nav-item${isActive(href) ? ' active' : ''}`

  const wsLabel = workspaceLabel(wsId)
  const wsAvatar = wsId.charAt(0).toUpperCase()

  return (
    <aside className="nav">
      <div className="nav-brand">
        <svg className="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M12 1.75 L20.25 4.75 V12 C20.25 17 16.5 20.75 12 22.25 C7.5 20.75 3.75 17 3.75 12 V4.75 Z"
            fill="#3b82f6"
          />
          <circle cx="12" cy="12.5" r="3.2" stroke="#07090e" strokeWidth="2" />
          <path d="M9.6 7 L12 4.5 L14.4 7 Z" fill="#07090e" />
        </svg>
        <span className="brand-wordmark">
          <span className="logo-own">own</span>
          <span className="logo-evo">Evo</span>
        </span>
      </div>

      <div className="workspace-switcher">
        <span className="workspace-avatar">{wsAvatar}</span>
        <span className="nav-label">{wsLabel}</span>
        <span className="chev">▾</span>
      </div>

      <div className="nav-section">Activity</div>
      <a href={root} className={cls(root)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M2 8 L5 8 L7 3 L9 13 L11 8 L14 8" />
        </svg>
        <span className="nav-label">Health</span>
      </a>
      <a href={`${root}/inbox`} className={cls(`${root}/inbox`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M2 4 L2 12 A1.5 1.5 0 0 0 3.5 13.5 L12.5 13.5 A1.5 1.5 0 0 0 14 12 L14 4 M2 4 L8 9 L14 4 M2 4 L14 4" />
        </svg>
        <span className="nav-label">Inbox</span>
      </a>
      <a href={`${root}/activity`} className={cls(`${root}/activity`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M2 13 L5 9 L8 11 L11 5 L14 8" />
          <circle cx="2" cy="13" r="1.2" />
          <circle cx="14" cy="8" r="1.2" />
        </svg>
        <span className="nav-label">Activity</span>
      </a>

      <div className="nav-section">Workflows</div>
      {workflows.map((w) => {
        const href = `${root}/workflows/${w.id}`
        return (
          <a key={w.id} href={href} className={`${cls(href)} nav-workflow`} title={w.description ?? w.id}>
            <svg className="nav-icon" viewBox="0 0 16 16">
              <path d="M3 3 L13 3 L13 13 L3 13 Z M3 7 L13 7 M7 7 L7 13" />
            </svg>
            <span className="nav-workflow-text">
              <span className="nav-label">
                {workflowDisplayTitle(w.id, w.description, 32)}
              </span>
              <span className="nav-workflow-id">{w.id}</span>
            </span>
          </a>
        )
      })}
      <a
        href={`${root}/workflows/new`}
        className={cls(`${root}/workflows/new`)}
        style={isActive(`${root}/workflows/new`) ? undefined : { color: 'var(--text-muted)' }}
      >
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M8 3 L8 13 M3 8 L13 8" />
        </svg>
        <span className="nav-label">New workflow</span>
      </a>

      <div className="nav-section">Library</div>
      <a href={`${root}/skills`} className={cls(`${root}/skills`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M2 3 L14 3 L14 13 L2 13 Z M2 6 L14 6" />
        </svg>
        <span className="nav-label">Skills</span>
      </a>
      <a href={`${root}/traces`} className={cls(`${root}/traces`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M3 11 L6 6 L9 9 L13 4" />
        </svg>
        <span className="nav-label">Traces</span>
      </a>
      <a href={`${root}/primitives`} className={cls(`${root}/primitives`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <rect x="2" y="2" width="5" height="5" />
          <rect x="9" y="2" width="5" height="5" />
          <rect x="2" y="9" width="5" height="5" />
          <rect x="9" y="9" width="5" height="5" />
        </svg>
        <span className="nav-label">Views</span>
      </a>
      <a href={`${root}/audit`} className={cls(`${root}/audit`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M3 4 L13 4 M3 8 L13 8 M3 12 L9 12" />
        </svg>
        <span className="nav-label">Audit</span>
      </a>

      <div className="nav-footer">{themeToggle}</div>
    </aside>
  )
}
