import type { FailureClusterSummary } from '../../../../../../lib/api'

interface CardProps {
  cluster: FailureClusterSummary
}

const SEVERITY_PILL: Record<string, string> = {
  high: 'pill red',
  medium: 'pill amber',
  low: 'pill outline',
}

// Card for one failure cluster. Visual target:
// www/preview/s26-rk7p3/16-failures.html § .cluster.
//
// The card is non-interactive in slice 3 (no proposal linkage yet).
// W8 polish will route the card to /proposals/[id] when a proposal
// exists for the cluster, and to a cluster-detail view otherwise.
export function FailureClusterCard({ cluster }: CardProps) {
  const idShort = cluster.id.slice(0, 8)
  const severityClass = SEVERITY_PILL[cluster.severity] ?? 'pill'

  return (
    <div className="cluster" style={{ textDecoration: 'none' }}>
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
          </div>
        </div>
      </div>
    </div>
  )
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toISOString().slice(0, 10)
}
