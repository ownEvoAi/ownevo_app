// Invite-accept landing page.
//
// Visited via a URL the inviter shares out-of-band. Two states:
//
//   1. Not signed in → redirect to /auth/signin with this page as the
//      callbackUrl so the user lands back here after signing in.
//   2. Signed in → render a confirmation card. The user clicks Accept,
//      which triggers `acceptInviteAction` (see ./actions.ts). On success
//      the action redirects into the new workspace; on error the form
//      surfaces a code-specific message and keeps the user on this page.
//
// The page itself does NOT call the kernel — that would auto-redeem on
// any visit (including the inviter testing the link in another tab). The
// kernel call is deferred to the explicit user action so accidental clicks
// don't consume the invite.
import { redirect } from 'next/navigation'
import { auth } from '@/auth'
import { AuthShell } from '../../auth/_components/AuthShell'
import { AcceptInviteForm } from './accept-form'

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

 return (
  <AuthShell>
   <h1 className="setup-title">Accept workspace invite</h1>
   <p className="setup-body">
    You&apos;ve been invited to join a workspace on ownEvo. Click Accept
    to add it to your account.
   </p>
   <AcceptInviteForm token={token} />
  </AuthShell>
 )
}
