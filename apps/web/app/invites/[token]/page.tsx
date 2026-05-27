// Invite-accept landing page.
//
// Visited via a URL the inviter shares out-of-band. The page calls the kernel
// preview endpoint to fetch invite metadata (workspace name, role, inviter,
// expiry) plus a viewer-relative status, then renders one of seven branches:
//
//   * invite_invalid (caught error) — bad signature or unknown token
//   * workspace_gone — addressed workspace was soft-deleted after mint
//   * revoked — admin revoked the invite
//   * expired — past the row's expires_at
//   * redeemed_by_other — different account already consumed it
//   * redeemed_by_me — same account already redeemed; offer to go to the
//        workspace rather than re-redeem
//   * email_mismatch — viewer is signed in to the wrong account; offer
//        sign-out + sign-in-as-the-right-address
//   * pending — happy path; show workspace/role/inviter/expiry + Accept
//
// The preview itself is a read; the kernel call that actually consumes the
// invite lives in acceptInviteAction so the URL is not burned by an inviter
// previewing it.
import Link from 'next/link'
import { redirect } from 'next/navigation'
import { auth } from '@/auth'
import { InviteRedeemError, previewInvite, type InvitePreview } from '@/lib/kernel-invites'
import { AuthShell } from '../../auth/_components/AuthShell'
import { AcceptInviteForm } from './accept-form'

function formatRoleLabel(role: string): string {
 if (role === 'admin') return 'an admin'
 return 'a member'
}

function formatExpiresIn(iso: string): string {
 const expiresAt = new Date(iso).getTime()
 const now = Date.now()
 const ms = expiresAt - now
 if (ms <= 0) return 'expiring now'
 const days = Math.floor(ms / 86_400_000)
 if (days >= 1) return `in ${days} day${days === 1 ? '' : 's'}`
 const hours = Math.floor(ms / 3_600_000)
 if (hours >= 1) return `in ${hours} hour${hours === 1 ? '' : 's'}`
 const minutes = Math.max(1, Math.floor(ms / 60_000))
 return `in ${minutes} minute${minutes === 1 ? '' : 's'}`
}

function workspaceLabel(preview: InvitePreview): string {
 return preview.workspace_name ?? 'this workspace'
}

function inviterLabel(preview: InvitePreview): string {
 return preview.invited_by_display_name ?? preview.invited_by_email ?? 'A workspace admin'
}

export default async function AcceptInvitePage({
 params,
}: {
 params: Promise<{ token: string }>
}) {
 const { token } = await params
 const session = await auth()
 if (!session?.user?.id) {
  const callback = `/invites/${encodeURIComponent(token)}`
  redirect(`/auth/signin?callbackUrl=${encodeURIComponent(callback)}`)
 }

 let preview: InvitePreview | null = null
 let previewError: { code: string; message: string } | null = null
 try {
  preview = await previewInvite(token, session.user.id)
 } catch (err) {
  if (err instanceof InviteRedeemError) {
   previewError = { code: err.code, message: err.message }
  } else {
   console.error('[invites/[token]] preview failed:', err)
   previewError = { code: 'unknown', message: 'Could not load this invite. Please try again.' }
  }
 }

 if (previewError || preview === null) {
  return (
   <AuthShell>
    <h1 className="setup-title">Invite link not valid</h1>
    <p className="setup-body">
     This invite link can&apos;t be opened. Ask the person who sent it for
     a fresh link.
    </p>
    <Link
     href="/"
     className="setup-secondary"
     style={{ marginTop: 12, textAlign: 'center', display: 'block' }}
    >
     Back to ownEvo
    </Link>
   </AuthShell>
  )
 }

 const wsLabel = workspaceLabel(preview)
 const roleLabel = formatRoleLabel(preview.role)
 const inviter = inviterLabel(preview)

 switch (preview.status) {
  case 'workspace_gone':
   return (
    <AuthShell>
     <h1 className="setup-title">Workspace no longer available</h1>
     <p className="setup-body">
      The workspace this invite was for has been deleted. Ask the person
      who invited you whether to expect a new link.
     </p>
    </AuthShell>
   )
  case 'revoked':
   return (
    <AuthShell>
     <h1 className="setup-title">Invite revoked</h1>
     <p className="setup-body">
      Your invite to <strong>{wsLabel}</strong> has been revoked by an
      admin. Ask {inviter} for a fresh link if you still need access.
     </p>
    </AuthShell>
   )
  case 'expired':
   return (
    <AuthShell>
     <h1 className="setup-title">Invite expired</h1>
     <p className="setup-body">
      Your invite to <strong>{wsLabel}</strong> has expired. Ask {inviter}{' '}
      for a fresh link.
     </p>
    </AuthShell>
   )
  case 'redeemed_by_other':
   return (
    <AuthShell>
     <h1 className="setup-title">Invite already used</h1>
     <p className="setup-body">
      This invite has already been used by another account. If you should
      be in <strong>{wsLabel}</strong>, ask {inviter} for a new link.
     </p>
    </AuthShell>
   )
  case 'redeemed_by_me':
   return (
    <AuthShell>
     <h1 className="setup-title">You&apos;re already a member</h1>
     <p className="setup-body">
      You&apos;ve already accepted this invite to <strong>{wsLabel}</strong>.
     </p>
     <Link
      href={`/workspaces/${preview.workspace_id}`}
      className="setup-submit"
      style={{ marginTop: 12, textAlign: 'center', display: 'block', textDecoration: 'none' }}
     >
      Go to {wsLabel}
     </Link>
    </AuthShell>
   )
  case 'email_mismatch': {
   const signedInAs = session.user.email ?? '(unknown)'
   const callbackAfterSignout = `/invites/${encodeURIComponent(token)}`
   return (
    <AuthShell>
     <h1 className="setup-title">Sign in with the right account</h1>
     <p className="setup-body">
      This invite to <strong>{wsLabel}</strong> was sent to{' '}
      <strong>{preview.invited_email}</strong>, but you&apos;re signed in
      as <strong>{signedInAs}</strong>. Sign out and sign back in with the
      invited address to accept it.
     </p>
     <form
      action={`/api/auth/signout?callbackUrl=${encodeURIComponent(callbackAfterSignout)}`}
      method="POST"
      style={{ marginTop: 12 }}
     >
      <button type="submit" className="setup-submit">
       Sign out
      </button>
     </form>
    </AuthShell>
   )
  }
  case 'pending':
  default:
   return (
    <AuthShell>
     <h1 className="setup-title">Join {wsLabel}</h1>
     <p className="setup-body">
      {inviter} invited you to join <strong>{wsLabel}</strong> as{' '}
      {roleLabel}. This invite expires {formatExpiresIn(preview.expires_at)}.
     </p>
     <AcceptInviteForm token={token} />
    </AuthShell>
   )
 }
}
