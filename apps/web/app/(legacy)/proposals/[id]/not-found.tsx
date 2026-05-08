import Link from 'next/link'

export default function ProposalNotFound() {
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
      <Link href="/inbox" className="btn btn-secondary">
        ← Back to inbox
      </Link>
    </div>
  )
}
