import { redirect } from 'next/navigation'

export default function HomePage {
 // : customer-facing workspace UI lives at /workspaces/[wsId]/...
 // Slug is cosmetic per D4 single-tenant; "acme" reads as a real
 // customer in screenshots vs the placeholder "default".
 redirect('/workspaces/acme')
}
