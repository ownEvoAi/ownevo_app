'use client'

import { usePathname } from 'next/navigation'

interface TabsProps {
 wsId: string
 wfId: string
 isBenchmark?: boolean
 // Force a particular tab to render as active regardless of pathname.
 // Used by surfaces that live outside the workflow route tree (the
 // workspace-scoped proposal detail page mounts these tabs to keep
 // the workflow context visible while drilling in).
 activeOverride?: 'overview' | 'spec' | 'operate' | 'eval-cases' | 'proposals' | 'failures' | 'traces' | 'audit' | 'triggers' | 'integrations' | 'permissions' | 'settings'
}

// Tab strip — Overview / Failures / Traces / Audit. Active state from
// usePathname(). Traces tab added in (7.1.9). Mock parity:
// `05-workflow-overview.html`.
//
// Benchmark workflows hide Operate (no production caller) and the
// production-only configuration tabs (Triggers / Integrations /
// Permissions / Settings). They keep Overview / Eval cases / Proposals
// / Failures / Traces / Audit — every surface that proves the loop is
// actually improving the agent.
export function WorkflowTabs({
 wsId,
 wfId,
 isBenchmark = false,
 activeOverride,
}: TabsProps) {
 const pathname = usePathname() ?? ''
 const root = `/workspaces/${wsId}/workflows/${wfId}`

 const allTabs: Array<{
 key: NonNullable<TabsProps['activeOverride']>
 href: string
 label: string
 hideOnBenchmark?: boolean
 }> = [
 { key: 'overview', href: root, label: 'Overview' },
 { key: 'spec', href: `${root}/spec`, label: 'Spec' },
 { key: 'operate', href: `${root}/operate`, label: 'Operate', hideOnBenchmark: true },
 { key: 'eval-cases', href: `${root}/eval-cases`, label: 'Eval cases' },
 { key: 'proposals', href: `${root}/proposals`, label: 'Proposals' },
 { key: 'failures', href: `${root}/failures`, label: 'Failures' },
 { key: 'traces', href: `${root}/traces`, label: 'Traces' },
 { key: 'audit', href: `${root}/audit`, label: 'Audit' },
 { key: 'triggers', href: `${root}/triggers`, label: 'Triggers', hideOnBenchmark: true },
 { key: 'integrations', href: `${root}/integrations`, label: 'Integrations', hideOnBenchmark: true },
 { key: 'permissions', href: `${root}/permissions`, label: 'Permissions', hideOnBenchmark: true },
 { key: 'settings', href: `${root}/settings`, label: 'Settings', hideOnBenchmark: true },
 ]
 const tabs = allTabs.filter((t) => !(isBenchmark && t.hideOnBenchmark))

 const isActive = (tab: (typeof allTabs)[number]) =>
 activeOverride !== undefined
 ? tab.key === activeOverride
 : tab.href === root
 ? pathname === root
 : pathname.startsWith(tab.href)

 return (
 <div className="tabs" style={{ marginBottom: 24 }}>
 {tabs.map((t) => (
 <a key={t.href} href={t.href} className={`tab${isActive(t) ? ' active' : ''}`}>
 {t.label}
 </a>
 ))}
 </div>
 )
}
