// Workspace provisioning screen.
//
// Shown to authenticated users who have no workspace membership yet — either
// on first login (brand-new Google account) or after all memberships have
// been removed. The middleware redirects here when session.workspaces is empty.
//
// Authenticated users with at least one workspace are redirected away by the
// middleware before they ever reach this page; it is only reachable without
// a workspace.
import { redirect } from 'next/navigation'
import { auth } from '@/auth'
import { WorkspaceCreateForm } from './workspace-create-form'

export default async function SetupWorkspacePage() {
 const session = await auth()

 // If somehow reached with a workspace (e.g. back-button after create),
 // send to the workspace root. Use the same predicate as the middleware
 // workspace gate (workspaces.length > 0) to avoid a state where the user
 // has memberships but activeWorkspaceId is null — fall back to the first
 // membership in that case.
 if (session?.workspaces?.length) {
  const target = session.activeWorkspaceId ?? session.workspaces[0]?.id
  redirect(`/workspaces/${target}`)
 }

 return (
  <div className="setup-shell">
   <div className="setup-card">
    <div className="setup-brand">
     <svg className="setup-brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
       d="M12 1.75 L20.25 4.75 V12 C20.25 17 16.5 20.75 12 22.25 C7.5 20.75 3.75 17 3.75 12 V4.75 Z"
       fill="#3b82f6"
      />
      <circle cx="12" cy="12.5" r="3.2" stroke="#07090e" strokeWidth="2" />
      <path d="M9.6 7 L12 4.5 L14.4 7 Z" fill="#07090e" />
     </svg>
     <span className="setup-brand-name">
      <span className="logo-own">own</span>
      <span className="logo-evo">Evo</span>
     </span>
    </div>

    <h1 className="setup-title">Create your workspace</h1>
    <p className="setup-body">
     A workspace holds your workflows, eval cases, and improvement history.
     Give it a name — you can rename it later.
    </p>

    <WorkspaceCreateForm />
   </div>
  </div>
 )
}
