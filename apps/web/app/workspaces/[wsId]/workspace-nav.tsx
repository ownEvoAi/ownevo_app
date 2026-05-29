'use client'

import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'
import { workflowDisplayTitle, workspaceLabel } from '../../../lib/format'
import type { WorkflowSummary } from '../../../lib/api'
import type { SyncedWorkspace } from '../../../lib/kernel-sync'
import { switchWorkspaceAction } from '../actions'

interface NavProps {
 wsId: string
 workflows: WorkflowSummary[]
 workspaces: SyncedWorkspace[]
 activeWorkspaceId: string | null
 themeToggle: ReactNode
}

// Sidebar for /workspaces/[wsId]/... routes. Active-item highlight
// derived from the current pathname; workflow list comes from the
// kernel (passed in from the workspace layout server component).
// Workspace memberships and the active workspace come from the session
// (also passed from the layout) so the switcher reflects real memberships.
export function WorkspaceNav({ wsId, workflows, workspaces, activeWorkspaceId, themeToggle }: NavProps) {
 const pathname = usePathname() ?? ''
 const root = `/workspaces/${wsId}`

 const isActive = (href: string) => {
 if (href === root) return pathname === root
 return pathname === href || pathname.startsWith(`${href}/`)
 }
 const cls = (href: string) => `nav-item${isActive(href) ? ' active' : ''}`

 // Resolve the display name for the active workspace from the membership list.
 // Fall back to the URL slug label so the nav always shows something even if
 // the session is momentarily stale (e.g. right after a workspace switch).
 const activeWorkspace = workspaces.find((w) => w.id === activeWorkspaceId)
 const wsLabel = activeWorkspace?.name ?? workspaceLabel(wsId)
 const wsAvatar = wsLabel.charAt(0).toUpperCase()

 // Partition by workflow.kind. Production workflows are real customer
 // surfaces; benchmarks (M5 forecasting, tau-bench replays) are kernel
 // validation runs that share the substrate. They get their own
 // sidebar section so the demo viewer doesn't confuse "Recalibrate
 // credit lines" (customer) with "M5-demand-prediction" (benchmark).
 const production = workflows.filter((w) => w.kind !== 'benchmark')
 const benchmarks = workflows.filter((w) => w.kind === 'benchmark')

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

 {workspaces.length > 1 ? (
  // Multiple workspaces: render a form-based switcher. The native <select>
  // submits to the switchWorkspaceAction server action on change, keeping
  // the switch flow entirely server-side without client-state management.
  <form action={switchWorkspaceAction} className="workspace-switcher workspace-switcher--select">
   <span className="workspace-avatar" aria-hidden>
    {wsAvatar}
   </span>
   <select
    name="workspaceId"
    defaultValue={activeWorkspaceId ?? wsId}
    onChange={(e) => {
     const form = e.target.form
     if (form) form.requestSubmit()
    }}
    className="workspace-select"
    aria-label="Switch workspace"
   >
    {workspaces.map((w) => (
     <option key={w.id} value={w.id}>
      {w.name}
     </option>
    ))}
   </select>
   <span className="chev" aria-hidden>
    ▾
   </span>
  </form>
 ) : (
  // Single workspace: no switcher needed — just the name.
  <div className="workspace-switcher">
   <span className="workspace-avatar" aria-hidden>
    {wsAvatar}
   </span>
   <span className="nav-label">{wsLabel}</span>
  </div>
 )}

 <div className="nav-section">Activity</div>
 <a href={root} className={cls(root)}>
 <svg className="nav-icon" viewBox="0 0 16 16">
 <path d="M2 8 L5 8 L7 3 L9 13 L11 8 L14 8" />
 </svg>
 <span className="nav-label">Health</span>
 </a>
 <a href={`${root}/inbox`} className={cls(`${root}/inbox`)}>
 <svg className="nav-icon" viewBox="0 0 16 16">
 <path d="M2 4 L14 4 L14 12 L2 12 Z M2 4 L8 9 L14 4" />
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
 <a href={`${root}/agents`} className={cls(`${root}/agents`)}>
 <svg className="nav-icon" viewBox="0 0 16 16">
 <circle cx="8" cy="5" r="2.5" />
 <path d="M3 13 C3 10 5 9 8 9 C11 9 13 10 13 13" />
 </svg>
 <span className="nav-label">Agents</span>
 </a>

 <div className="nav-section">Workflows</div>
 {production.map((w) => {
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

 {benchmarks.length > 0 && (
 <>
 <div className="nav-section">
 Benchmarks
 <span className="nav-section-hint" title="Kernel validation runs — not customer workflows">
 ⓘ
 </span>
 </div>
 {benchmarks.map((w) => {
 const href = `${root}/workflows/${w.id}`
 return (
 <a key={w.id} href={href} className={`${cls(href)} nav-workflow`} title={w.description ?? w.id}>
 <svg className="nav-icon" viewBox="0 0 16 16">
 <path d="M2 13 L2 3 L4 3 L4 13 M6 13 L6 7 L8 7 L8 13 M10 13 L10 5 L12 5 L12 13" />
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
 </>
 )}

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

 <div className="nav-section">Records</div>
 <a href={`${root}/traces`} className={cls(`${root}/traces`)}>
 <svg className="nav-icon" viewBox="0 0 16 16">
 <path d="M3 11 L6 6 L9 9 L13 4" />
 </svg>
 <span className="nav-label">Traces</span>
 </a>
 <a href={`${root}/jobs`} className={cls(`${root}/jobs`)}>
 <svg className="nav-icon" viewBox="0 0 16 16">
 <rect x="3" y="3" width="10" height="3" />
 <rect x="3" y="7" width="10" height="3" />
 <rect x="3" y="11" width="10" height="3" />
 </svg>
 <span className="nav-label">Jobs</span>
 </a>
 <a href={`${root}/audit`} className={cls(`${root}/audit`)}>
 <svg className="nav-icon" viewBox="0 0 16 16">
 <path d="M3 4 L13 4 M3 8 L13 8 M3 12 L9 12" />
 </svg>
 <span className="nav-label">Audit</span>
 </a>

 <div className="nav-section">Settings</div>
 <a
 href={`${root}/settings/members`}
 className={cls(`${root}/settings/members`)}
 >
 <svg className="nav-icon" viewBox="0 0 16 16" aria-hidden="true">
 <circle cx="5.5" cy="6" r="2" />
 <circle cx="11" cy="7" r="1.6" />
 <path d="M1.5 13 C1.5 10.5 3.5 9.5 5.5 9.5 C7.5 9.5 9.5 10.5 9.5 13" />
 <path d="M9 12.5 C9 10.8 10.2 10 11 10 C12.5 10 13.8 11 14 12.5" />
 </svg>
 <span className="nav-label">Members</span>
 </a>
 <a
 href={`${root}/settings/integrations`}
 className={cls(`${root}/settings/integrations`)}
 >
 <svg className="nav-icon" viewBox="0 0 16 16">
 <path d="M6 2 L10 2 L10 4 L13 5 L12 8 L14 10 L11 12 L10 14 L6 14 L5 12 L2 10 L4 8 L3 5 L6 4 Z" />
 <circle cx="8" cy="8" r="2" />
 </svg>
 <span className="nav-label">Integrations</span>
 </a>

 <div className="nav-footer">{themeToggle}</div>
 </aside>
 )
}
