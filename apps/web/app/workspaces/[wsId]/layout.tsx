import type { ReactNode } from 'react'
import { ThemeToggle } from '../../components/theme-toggle'
import { listWorkflows, type WorkflowSummary } from '../../../lib/api'
import { WorkspaceNav } from './workspace-nav'

interface LayoutProps {
 children: ReactNode
 params: Promise<{ wsId: string }>
}

// customer-facing workspace shell. Sidebar lifted from
// § Sidebar nav.
//
// The wsId URL param is cosmetic for MVP — D4 single-tenant means the
// backend ignores it. The slug shows in the address bar during the live
// demo (default value: "acme"). The URL contract is stable — the backend
// uses it for workspace scoping once the auth layer resolves workspace_id.
export default async function WorkspaceLayout({ children, params }: LayoutProps) {
 const { wsId } = await params

 let workflows: WorkflowSummary[] = []
 try {
 workflows = (await listWorkflows()).items
 } catch {
 // Sidebar still renders without the workflow list — Health page
 // surfaces the API-down banner.
 }

 return (
 <div className="app-shell">
 <WorkspaceNav
 wsId={wsId}
 workflows={workflows}
 themeToggle={<ThemeToggle />}
 />
 <main className="main">{children}</main>
 </div>
 )
}
