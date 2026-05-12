'use client'

import { usePathname } from 'next/navigation'

interface TabsProps {
  wsId: string
  wfId: string
  isBenchmark?: boolean
}

// Tab strip — Overview / Failures / Traces / Audit. Active state from
// usePathname. Traces tab added in W7 slice 8 (7.1.9). Mock parity:
// `05-workflow-overview.html`.
//
// Benchmark workflows hide Operate (no production caller) and the
// production-only configuration tabs (Triggers / Integrations /
// Permissions / Settings). They keep Overview / Eval cases / Proposals
// / Failures / Traces / Audit — every surface that proves the loop is
// actually improving the agent.
export function WorkflowTabs({ wsId, wfId, isBenchmark = false }: TabsProps) {
  const pathname = usePathname() ?? ''
  const root = `/workspaces/${wsId}/workflows/${wfId}`

  const allTabs: Array<{ href: string; label: string; hideOnBenchmark?: boolean }> = [
    { href: root, label: 'Overview' },
    { href: `${root}/operate`, label: 'Operate', hideOnBenchmark: true },
    { href: `${root}/eval-cases`, label: 'Eval cases' },
    { href: `${root}/proposals`, label: 'Proposals' },
    { href: `${root}/failures`, label: 'Failures' },
    { href: `${root}/traces`, label: 'Traces' },
    { href: `${root}/audit`, label: 'Audit' },
    { href: `${root}/triggers`, label: 'Triggers', hideOnBenchmark: true },
    { href: `${root}/integrations`, label: 'Integrations', hideOnBenchmark: true },
    { href: `${root}/permissions`, label: 'Permissions', hideOnBenchmark: true },
    { href: `${root}/settings`, label: 'Settings', hideOnBenchmark: true },
  ]
  const tabs = allTabs.filter((t) => !(isBenchmark && t.hideOnBenchmark))

  const isActive = (href: string) =>
    href === root ? pathname === root : pathname.startsWith(href)

  return (
    <div className="tabs" style={{ marginBottom: 24 }}>
      {tabs.map((t) => (
        <a key={t.href} href={t.href} className={`tab${isActive(t.href) ? ' active' : ''}`}>
          {t.label}
        </a>
      ))}
    </div>
  )
}
