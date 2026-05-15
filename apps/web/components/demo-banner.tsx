import { isDemoMode } from '../lib/demo-mode'

export function DemoBanner() {
  if (!isDemoMode()) return null
  return (
    <div
      role="status"
      aria-label="Read-only demo banner"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 50,
        padding: '6px 16px',
        fontSize: 12,
        lineHeight: 1.4,
        background: 'var(--surface-2, #fef3c7)',
        color: 'var(--text, #1f2937)',
        borderBottom: '1px solid var(--border, #fcd34d)',
        textAlign: 'center',
      }}
    >
      Read-only demo. Approve, reject, deploy, and iteration actions are
      disabled. Self-host from{' '}
      <a
        href="https://github.com/ownEvoAi/ownevo_app"
        target="_blank"
        rel="noreferrer"
        style={{ textDecoration: 'underline' }}
      >
        GitHub
      </a>
      .
    </div>
  )
}
