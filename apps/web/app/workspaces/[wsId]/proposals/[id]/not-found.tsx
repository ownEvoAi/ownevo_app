import Link from 'next/link'

interface Props {
  params?: Promise<{ wsId: string }>
}

// Next.js' built-in not-found.tsx receives no params. We render a
// generic back-to-workspace link by inferring the wsId from the URL
// (Next 14+ exposes it via the layout, but the simplest portable path
// is a hardcoded fallback to "acme" — the cosmetic D4 single-tenant
// slug used everywhere else in the workspace shell).
export default function ProposalNotFound(_: Props) {
  const wsHref = `/workspaces/acme`
  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Proposal not found</h1>
          <p className="page-subtitle">
            The proposal id was not in the kernel database. It may have been
            deleted, or the id was mistyped.
          </p>
        </div>
      </header>
      <Link href={wsHref} className="btn btn-secondary">
        ← Back to workspace
      </Link>
    </div>
  )
}
