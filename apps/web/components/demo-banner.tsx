import { getDemoStatus } from '../lib/demo-status'

// Async server component — reads the live demo status from the kernel
// so the banner reflects the visitor's tier and current quota usage.
//
// Copy variants:
//   * Anonymous, fresh:   "Demo · approve actions disabled. Design + generate open with a daily quota."
//   * Anonymous, partial: "Demo · 14% of daily quota used."
//   * Anonymous, used:    "Demo · daily quota used. Paste an invite token, or come back tomorrow."
//   * Elevated:           "Demo · invited (label) until <date>."
//   * Unlimited:          "Demo · unlimited invite (label)."
//   * Global cap hit:     "Demo · today's LLM budget is exhausted — back tomorrow."
//
// Outside DEMO_MODE the banner renders nothing.
export async function DemoBanner() {
  const status = await getDemoStatus()
  if (!status.demoMode) return null

  const { copy, tone } = renderBanner(status)

  return (
    <div
      role="status"
      aria-label="Demo banner"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 50,
        padding: '6px 16px',
        fontSize: 12,
        lineHeight: 1.4,
        background: tone === 'warn' ? 'var(--surface-2, #fef3c7)' : 'var(--surface-2, #e0f2fe)',
        color: 'var(--text, #1f2937)',
        borderBottom:
          tone === 'warn'
            ? '1px solid var(--border, #fcd34d)'
            : '1px solid var(--border, #93c5fd)',
        textAlign: 'center',
      }}
    >
      {copy}{' '}
      <a
        href="https://github.com/ownEvoAi/ownevo_app"
        target="_blank"
        rel="noreferrer"
        style={{ textDecoration: 'underline' }}
      >
        self-host
      </a>
      .
    </div>
  )
}

function renderBanner(status: {
  tier: 'anonymous' | 'elevated' | 'unlimited' | null
  label: string | null
  usedTokens: number
  limitTokens: number | null
  exhausted: boolean
  budgetExhausted: boolean
  inviteExp: number | null
}): { copy: string; tone: 'info' | 'warn' } {
  if (status.budgetExhausted) {
    return {
      copy: "Demo · today's LLM budget is exhausted — back tomorrow.",
      tone: 'warn',
    }
  }
  if (status.tier === 'unlimited') {
    return {
      copy: `Demo · unlimited invite${status.label ? ` (${status.label})` : ''}.`,
      tone: 'info',
    }
  }
  if (status.tier === 'elevated') {
    const until =
      status.inviteExp != null
        ? ` until ${new Date(status.inviteExp * 1000).toLocaleDateString()}`
        : ''
    return {
      copy: `Demo · invited${status.label ? ` (${status.label})` : ''}${until}.`,
      tone: 'info',
    }
  }
  // Anonymous
  if (status.exhausted) {
    return {
      copy: 'Demo · daily quota used. Paste an invite token to continue, or come back tomorrow.',
      tone: 'warn',
    }
  }
  if (status.limitTokens && status.usedTokens > 0) {
    const pct = Math.min(99, Math.round((status.usedTokens / status.limitTokens) * 100))
    return { copy: `Demo · ${pct}% of daily quota used.`, tone: 'info' }
  }
  return {
    copy: 'Demo · approve actions disabled. Design + generate are open with a daily quota.',
    tone: 'info',
  }
}
