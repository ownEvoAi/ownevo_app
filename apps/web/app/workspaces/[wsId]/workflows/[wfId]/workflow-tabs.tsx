'use client'

import { usePathname } from 'next/navigation'

interface TabsProps {
  wsId: string
  wfId: string
}

// Tab strip — Overview / Failures / Traces / Audit. Active state from
// usePathname. Traces tab added in W7 slice 8 (7.1.9). Mock parity:
// `05-workflow-overview.html`.
export function WorkflowTabs({ wsId, wfId }: TabsProps) {
  const pathname = usePathname() ?? ''
  const root = `/workspaces/${wsId}/workflows/${wfId}`

  const tabs: Array<{ href: string; label: string }> = [
    { href: root, label: 'Overview' },
    { href: `${root}/eval-cases`, label: 'Eval cases' },
    { href: `${root}/proposals`, label: 'Proposals' },
    { href: `${root}/failures`, label: 'Failures' },
    { href: `${root}/traces`, label: 'Traces' },
    { href: `${root}/audit`, label: 'Audit' },
    { href: `${root}/settings`, label: 'Settings' },
  ]

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
