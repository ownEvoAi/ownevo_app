import type { ReactNode } from 'react'

interface LayoutProps {
  children: ReactNode
}

// Operator shell — minimal layout, no AgentOS sidebar. The operator
// shell is a separate product surface (mock parity: s26-rk7p3/28..31):
// the domain expert reviewing what the agent has produced, without
// the improvement-loop chrome. The shell is intentionally lean — top
// bar + content, no workflow nav, no settings, no audit tab. Those
// belong to the AgentOS shell which links here via "open agent UI →"
// on the workflow Overview.
export default function OperatorLayout({ children }: LayoutProps) {
  return <div className="op-shell">{children}</div>
}
