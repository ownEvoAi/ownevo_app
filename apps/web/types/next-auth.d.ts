// Module augmentation: carry the resolved kernel principal (internal user id
// + workspace memberships + active workspace) on the session.
//
// The JWT carrier fields (userId / activeWorkspaceId / workspaces) are written
// in the `jwt` callback and read back with typeof guards in `session`, so they
// ride the default JWT index signature and need no separate augmentation here.
import type { DefaultSession } from 'next-auth'
import type { SyncedWorkspace } from '@/lib/kernel-sync'

declare module 'next-auth' {
  interface Session {
    activeWorkspaceId: string | null
    workspaces: SyncedWorkspace[]
    user: {
      id: string
    } & DefaultSession['user']
  }
}
