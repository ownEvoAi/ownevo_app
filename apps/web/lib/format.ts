// Tiny formatting helpers — server-rendered, locale-aware.
//
// Kept in lib/ rather than colocated with pages because both the inbox
// list and the proposal detail page render timestamps and scores; a
// single source of truth for "5h ago" beats two off-by-one drifts.

export function relativeTime(iso: string, now: Date = new Date()): string {
  const t = new Date(iso).getTime()
  const dt = (now.getTime() - t) / 1000
  if (dt < 60) return 'just now'
  if (dt < 3600) return `${Math.round(dt / 60)}m ago`
  if (dt < 86400) return `${Math.round(dt / 3600)}h ago`
  return `${Math.round(dt / 86400)}d ago`
}

export function formatScore(value: number | null, digits = 4): string {
  return value === null ? '—' : value.toFixed(digits)
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
