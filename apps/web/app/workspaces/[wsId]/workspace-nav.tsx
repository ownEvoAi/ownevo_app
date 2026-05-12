'use client'

import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'
import { workspaceLabel } from '../../../lib/format'

interface NavProps {
  wsId: string
  themeToggle: ReactNode
}

// Sidebar for /workspaces/[wsId]/... routes. Active-item highlight
// derived from the current pathname; the rest is static markup
// matching www/preview/s26-rk7p3/01-health.html.
//
// Workflow IDs are intentionally hard-coded for MVP — the investor programdemo
// shows demand-prediction (live) + labour/contract/support (mocks).
// Multi-tenant retrofit (TODO-1) replaces this with a workspace-
// scoped query.
//
// COUPLING: the IDs `labour`, `contract`, `support` must stay in sync
// with the keys in `workflows/[wfId]/mocks.ts` (WORKFLOW_MOCKS). If a
// mock is renamed there, update the matching <a href> below.
export function WorkspaceNav({ wsId, themeToggle }: NavProps) {
  const pathname = usePathname() ?? ''
  const root = `/workspaces/${wsId}`

  const isActive = (href: string) => {
    if (href === root) return pathname === root
    return pathname === href || pathname.startsWith(`${href}/`)
  }
  const cls = (href: string) => `nav-item${isActive(href) ? ' active' : ''}`

  // Workspace label is cosmetic until D4 retrofit.
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

      <div className="nav-section">Workflows</div>
      <a
        href={`${root}/workflows/m5-demand-prediction`}
        className={cls(`${root}/workflows/m5-demand-prediction`)}
      >
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M3 3 L13 3 L13 13 L3 13 Z M3 7 L13 7 M7 7 L7 13" />
        </svg>
        <span className="nav-label">Demand prediction</span>
      </a>
      <a href={`${root}/workflows/labour`} className={cls(`${root}/workflows/labour`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M3 3 L13 3 L13 13 L3 13 Z M3 7 L13 7 M7 7 L7 13" />
        </svg>
        <span className="nav-label">Labour management</span>
      </a>
      <a href={`${root}/workflows/contract`} className={cls(`${root}/workflows/contract`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M3 3 L13 3 L13 13 L3 13 Z M3 7 L13 7 M7 7 L7 13" />
        </svg>
        <span className="nav-label">Union contract review</span>
      </a>
      <a href={`${root}/workflows/support`} className={cls(`${root}/workflows/support`)}>
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M3 3 L13 3 L13 13 L3 13 Z M3 7 L13 7 M7 7 L7 13" />
        </svg>
        <span className="nav-label">Customer support</span>
      </a>
      <a
        href={`${root}/workflows/tau3-retail-v1`}
        className={cls(`${root}/workflows/tau3-retail-v1`)}
      >
        <svg className="nav-icon" viewBox="0 0 16 16">
          <path d="M3 3 L13 3 L13 13 L3 13 Z M3 7 L13 7 M7 7 L7 13" />
        </svg>
        <span className="nav-label">τ³-bench retail</span>
      </a>
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
