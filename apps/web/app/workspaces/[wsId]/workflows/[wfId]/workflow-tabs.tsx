'use client'

import { usePathname } from 'next/navigation'

interface TabsProps {
  wsId: string
  wfId: string
}

// Simple tab strip — Overview / Failures / Audit. Active state from
// usePathname. Fewer tabs than the mock (`05-workflow-overview.html`)
// since W7 only covers the demo critical path.
export function WorkflowTabs({ wsId, wfId }: TabsProps) {
  const pathname = usePathname() ?? ''
  const root = `/workspaces/${wsId}/workflows/${wfId}`

  const tabs: Array<{ href: string; label: string }> = [
    { href: root, label: 'Overview' },
    { href: `${root}/failures`, label: 'Failures' },
    { href: `${root}/audit`, label: 'Audit' },
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
