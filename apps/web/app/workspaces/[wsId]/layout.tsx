import type { ReactNode } from 'react'
import { ThemeToggle } from '../../components/theme-toggle'
import { listWorkflows, type WorkflowSummary } from '../../../lib/api'
import { auth } from '@/auth'
import type { SyncedWorkspace } from '@/lib/kernel-sync'
import { WorkspaceNav } from './workspace-nav'

interface LayoutProps {
 children: ReactNode
 params: Promise<{ wsId: string }>
}

// Customer-facing workspace shell with sidebar.
// Reads the session to supply the workspace switcher with the user's full
// membership list and the currently active workspace.
export default async function WorkspaceLayout({ children, params }: LayoutProps) {
 const { wsId } = await params

 // auth() and listWorkflows() can run in parallel — neither depends on
 // the other.
 const [session, workflowResult] = await Promise.allSettled([
  auth(),
  listWorkflows().then((r) => r.items),
 ])

 const workspaces: SyncedWorkspace[] =
  session.status === 'fulfilled' && Array.isArray(session.value?.workspaces)
   ? (session.value.workspaces as SyncedWorkspace[])
   : []

 const activeWorkspaceId: string | null =
  session.status === 'fulfilled'
   ? (session.value?.activeWorkspaceId ?? null)
   : null

 const workflows: WorkflowSummary[] =
  workflowResult.status === 'fulfilled' ? workflowResult.value : []

 return (
  <div className="app-shell">
   <WorkspaceNav
    wsId={wsId}
    workflows={workflows}
    workspaces={workspaces}
    activeWorkspaceId={activeWorkspaceId}
    themeToggle={<ThemeToggle />}
   />
   <main className="main">{children}</main>
  </div>
 )
}
