import Link from 'next/link'
import type { FailureSource } from '../../../../../../lib/api'

interface ControlsProps {
  wsId: string
  wfId: string
  view: 'cluster' | 'list'
  source: FailureSource | null
  totalProd: number
  totalEval: number
}

// 9.2.1 — control strip above the failures view. Two control groups:
//   1. Source filter (All / Production / Eval) — scope the visible
//      failures to one source. Both views honor it.
//   2. View toggle (Cluster / List) — switch between the existing
//      cluster cards and a flat sortable table of individual failures.
// State lives in URL search params so the server component can render
// the right shape per request; refresh-safe and shareable.
export function FailuresControls({
  wsId,
  wfId,
  view,
  source,
  totalProd,
  totalEval,
}: ControlsProps) {
  const base = `/workspaces/${wsId}/workflows/${wfId}/failures`
  const params = (next: Partial<{ view: string; source: string }>) => {
    const v = next.view ?? view
    const s = 'source' in next ? next.source : source ?? ''
    const qs = new URLSearchParams()
    if (v === 'list') qs.set('view', 'list')
    if (s) qs.set('source', s)
    const q = qs.toString()
    return q ? `${base}?${q}` : base
  }

  const totalAll = totalProd + totalEval

  return (
    <div className="failures-controls">
      <div className="control-group" role="group" aria-label="Source filter">
        <Link
          href={params({ source: '' })}
          className={`control-chip${source === null ? ' active' : ''}`}
        >
          All <span className="control-count">{totalAll}</span>
        </Link>
        <Link
          href={params({ source: 'production' })}
          className={`control-chip${source === 'production' ? ' active' : ''}`}
        >
          Production <span className="control-count">{totalProd}</span>
        </Link>
        <Link
          href={params({ source: 'eval' })}
          className={`control-chip${source === 'eval' ? ' active' : ''}`}
        >
          Eval <span className="control-count">{totalEval}</span>
        </Link>
      </div>

      <div className="control-group" role="group" aria-label="View toggle">
        <Link
          href={params({ view: 'cluster' })}
          className={`control-chip${view === 'cluster' ? ' active' : ''}`}
        >
          Cluster view
        </Link>
        <Link
          href={params({ view: 'list' })}
          className={`control-chip${view === 'list' ? ' active' : ''}`}
        >
          List view
        </Link>
      </div>
    </div>
  )
}
