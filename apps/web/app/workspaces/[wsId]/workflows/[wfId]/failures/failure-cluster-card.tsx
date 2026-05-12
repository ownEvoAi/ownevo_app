import Link from 'next/link'
import type { FailureClusterSummary } from '../../../../../../lib/api'

interface CardProps {
  cluster: FailureClusterSummary
  wsId: string
  wfId: string
}

const SEVERITY_PILL: Record<string, string> = {
  high: 'pill red',
  medium: 'pill amber',
  low: 'pill outline',
}

// Card for one failure cluster. Visual target:
// www/preview/s26-rk7p3/16-failures.html § .cluster.
//
// W7 slice 7 (7.1.4) — when a proposal has been spawned against the
// cluster (`latest_proposal_id` non-null), the cluster header becomes a
// link to the proposal-detail surface so the YC demo flow is one
// click: cluster → proposal → approve. When no proposal exists yet,
// the header stays non-interactive.
//
// A separate footer link points back at the iteration that spawned
// the cluster — resolved via the cluster's sample traces. Two adjacent
// links in the same card avoids nested-anchor invalid markup.
export function FailureClusterCard({ cluster, wsId, wfId }: CardProps) {
  const idShort = cluster.id.slice(0, 8)
  const severityClass = SEVERITY_PILL[cluster.severity] ?? 'pill'
  const proposalHref = cluster.latest_proposal_id
    ? `/workspaces/${wsId}/proposals/${cluster.latest_proposal_id}`
    : null
  const iterationHref =
    cluster.spawning_iteration_index !== null &&
    cluster.spawning_iteration_index !== undefined
      ? `/workspaces/${wsId}/workflows/${wfId}/iterations/${cluster.spawning_iteration_index}`
      : null

  const header = (
    <div className="cluster-row">
      <div>
        <div className="cluster-title">{cluster.label}</div>
        <div className="cluster-id">cluster #{idShort}</div>
        <div className="cluster-meta-row">
          <span className={severityClass}>
            {cluster.severity[0].toUpperCase() + cluster.severity.slice(1)}
          </span>
          <span>
            {cluster.cluster_size} trace{cluster.cluster_size === 1 ? '' : 's'}
          </span>
          <span>·</span>
          <span>First seen {formatDate(cluster.created_at)}</span>
          {cluster.label_eval_score !== null && (
            <>
              <span>·</span>
              <span>Label conf. {cluster.label_eval_score.toFixed(2)}</span>
            </>
          )}
          {cluster.quality_score !== null && (
            <>
              <span>·</span>
              <span>HDBSCAN persistence {cluster.quality_score.toFixed(2)}</span>
            </>
          )}
          {proposalHref && (
            <>
              <span>·</span>
              <span className="cluster-cta">View proposal →</span>
            </>
          )}
        </div>
      </div>
    </div>
  )

  const headerEl = proposalHref ? (
    <Link
      href={proposalHref}
      className="cluster-header-link"
      style={{ textDecoration: 'none', display: 'block', color: 'inherit' }}
    >
      {header}
    </Link>
  ) : (
    header
  )

  const footer = iterationHref ? (
    <div className="cluster-footer">
      <Link href={iterationHref} className="cluster-footer-link">
        ← From iteration #{cluster.spawning_iteration_index}
      </Link>
    </div>
  ) : null

  return (
    <div
      className={`cluster${proposalHref ? ' cluster-link' : ''}`}
      style={{ textDecoration: 'none' }}
    >
      {headerEl}
      {footer}
    </div>
  )
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toISOString().slice(0, 10)
}
