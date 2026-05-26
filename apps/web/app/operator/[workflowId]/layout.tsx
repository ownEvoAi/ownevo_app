import type { ReactNode } from 'react'

interface LayoutProps {
 children: ReactNode
}

// Operator shell — minimal layout, no workspace sidebar. The operator
// shell is a separate product surface (mock parity: s26-rk7p3/28..31):
// the domain expert reviewing what the agent has produced, without
// the improvement-loop chrome. The shell is intentionally lean — top
// bar + content, no workflow nav, no settings, no audit tab. Those
// belong to the owner shell which links here via "Open operator view ↗"
// on the workflow header.
export default function OperatorLayout({ children }: LayoutProps) {
 return <div className="op-shell">{children}</div>
}
