import { redirect } from 'next/navigation'

export default function HomePage() {
  // The approval queue scaffold has one entry surface — the inbox.
  // W5 polish adds a workspace-level dashboard; until then, route
  // root traffic straight there.
  redirect('/inbox')
}
