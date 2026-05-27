import { redirect } from 'next/navigation'
import { auth } from '@/auth'
import {
 listPendingInvites,
 listWorkspaceMembers,
 type PendingInvite,
 type WorkspaceMember,
} from '@/lib/kernel-invites'
import { InviteMemberForm } from './invite-form'
import { RevokeInviteButton } from './revoke-button'

interface PageProps {
 params: Promise<{ wsId: string }>
}

function formatRole(role: string): string {
 if (role === 'owner') return 'Owner'
 if (role === 'admin') return 'Admin'
 return 'Member'
}

function formatExpiresIn(iso: string): string {
 const expiresAt = new Date(iso).getTime()
 const now = Date.now()
 const ms = expiresAt - now
 if (ms <= 0) return 'expiring now'
 const days = Math.floor(ms / 86_400_000)
 if (days >= 1) return `expires in ${days} day${days === 1 ? '' : 's'}`
 const hours = Math.floor(ms / 3_600_000)
 if (hours >= 1) return `expires in ${hours} hour${hours === 1 ? '' : 's'}`
 const minutes = Math.max(1, Math.floor(ms / 60_000))
 return `expires in ${minutes} minute${minutes === 1 ? '' : 's'}`
}

export default async function MembersPage({ params }: PageProps) {
 const { wsId } = await params
 const session = await auth()
 if (!session?.user?.id) {
  redirect(`/auth/signin?callbackUrl=${encodeURIComponent(`/workspaces/${wsId}/settings/members`)}`)
 }

 // Derive the viewer's role from the session — the layout already feeds the
 // membership list in. If the session is stale and doesn't show this
 // workspace, the kernel calls below will 403 and the page renders an error.
 const memberships = Array.isArray(session.workspaces) ? session.workspaces : []
 const myMembership = memberships.find((w) => w.id === wsId)
 const myRole = myMembership?.role ?? null
 const isAdmin = myRole === 'owner' || myRole === 'admin'

 let members: WorkspaceMember[] = []
 let membersError: string | null = null
 try {
  members = await listWorkspaceMembers(wsId, session.user.id)
 } catch (err) {
  console.error('[members page] list members failed:', err)
  membersError = 'Could not load workspace members.'
 }

 let invites: PendingInvite[] = []
 let invitesError: string | null = null
 if (isAdmin) {
  try {
   invites = await listPendingInvites(wsId, session.user.id)
  } catch (err) {
   console.error('[members page] list invites failed:', err)
   invitesError = 'Could not load pending invites.'
  }
 }

 const cellStyle = { padding: '10px 12px', borderBottom: '1px solid var(--border)' } as const
 const headerCellStyle = {
  padding: '8px 12px',
  textAlign: 'left' as const,
  fontSize: 11,
  textTransform: 'uppercase' as const,
  letterSpacing: '0.08em',
  color: 'var(--text-muted)',
  borderBottom: '1px solid var(--border)',
 }

 return (
  <>
   <h1 className="page-title">Members</h1>
   <p className="page-subtitle" style={{ marginBottom: 24 }}>
    Workspace members and pending invites. Owners and admins can invite
    new members and revoke outstanding invites.
   </p>

   <section style={{ marginBottom: 32 }}>
    <div className="section-title" style={{ marginBottom: 8 }}>
     Active members{members.length > 0 ? ` (${members.length})` : ''}
    </div>
    {membersError ? (
     <p role="alert" className="settings-error">
      {membersError}
     </p>
    ) : (
     <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
       <tr>
        <th style={headerCellStyle}>Email</th>
        <th style={headerCellStyle}>Role</th>
        <th style={headerCellStyle}>Joined</th>
       </tr>
      </thead>
      <tbody>
       {members.map((m) => (
        <tr key={m.user_id}>
         <td style={cellStyle}>
          {m.display_name ? (
           <>
            <span style={{ fontWeight: 500 }}>{m.display_name}</span>
            <br />
            <span style={{ color: 'var(--text-muted)' }}>{m.email}</span>
           </>
          ) : (
           m.email
          )}
         </td>
         <td style={cellStyle}>{formatRole(m.role)}</td>
         <td style={{ ...cellStyle, color: 'var(--text-muted)' }}>
          {new Date(m.joined_at).toLocaleDateString()}
         </td>
        </tr>
       ))}
      </tbody>
     </table>
    )}
   </section>

   {isAdmin ? (
    <>
     <section style={{ marginBottom: 32 }}>
      <div className="section-title" style={{ marginBottom: 8 }}>
       Pending invites
      </div>
      {invitesError ? (
       <p role="alert" className="settings-error">
        {invitesError}
       </p>
      ) : invites.length === 0 ? (
       <p style={{ color: 'var(--text-muted)' }}>
        No outstanding invites.
       </p>
      ) : (
       <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
         <tr>
          <th style={headerCellStyle}>Email</th>
          <th style={headerCellStyle}>Role</th>
          <th style={headerCellStyle}>Invited by</th>
          <th style={headerCellStyle}>Expiry</th>
          <th style={headerCellStyle} />
         </tr>
        </thead>
        <tbody>
         {invites.map((inv) => (
          <tr key={inv.invite_id}>
           <td style={cellStyle}>{inv.invited_email}</td>
           <td style={cellStyle}>{formatRole(inv.role)}</td>
           <td style={{ ...cellStyle, color: 'var(--text-muted)' }}>
            {inv.invited_by_display_name ?? inv.invited_by_email ?? inv.invited_by_user_id}
           </td>
           <td style={{ ...cellStyle, color: 'var(--text-muted)' }}>
            {formatExpiresIn(inv.expires_at)}
           </td>
           <td style={{ ...cellStyle, textAlign: 'right' }}>
            <RevokeInviteButton
             workspaceId={wsId}
             inviteId={inv.invite_id}
             invitedEmail={inv.invited_email}
            />
           </td>
          </tr>
         ))}
        </tbody>
       </table>
      )}
     </section>
     <InviteMemberForm workspaceId={wsId} />
    </>
   ) : (
    <p style={{ color: 'var(--text-muted)' }}>
     Only owners and admins can invite new members.
    </p>
   )}
  </>
 )
}
