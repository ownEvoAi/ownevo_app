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

// Title-case a workspace slug for display in the nav + page subtitle.
// Cosmetic only — the slug is ignored by the backend per D4.
export function workspaceLabel(slug: string): string {
  return slug.charAt(0).toUpperCase() + slug.slice(1)
}

// Short display label for a workflow. Descriptions are free-form prose
// (often multi-paragraph), so the first sentence / first N chars makes a
// usable list label. Falls back to the id if description is empty.
export function workflowDisplayTitle(
  id: string,
  description: string | null | undefined,
  maxLen = 60,
): string {
  if (!description) return id
  const firstSentence = description.split(/(?<=[.!?])\s/, 1)[0] ?? description
  const trimmed = firstSentence.trim()
  if (trimmed.length <= maxLen) return trimmed
  // Word-boundary truncation — find the last space within the budget so
  // we don't cut a word in half (TODO-40). Falls back to hard cut when
  // there's no space (e.g. a long URL or single-word title).
  const sliced = trimmed.slice(0, maxLen - 1)
  const lastSpace = sliced.lastIndexOf(' ')
  if (lastSpace > maxLen / 2) {
    return sliced.slice(0, lastSpace).trimEnd() + '…'
  }
  return sliced.trimEnd() + '…'
}
