import type { ReactNode } from 'react'
import { ThemeToggle } from '../components/theme-toggle'

// (legacy) route group — wraps the W2.5/W5.5 flat routes (/inbox,
// /proposals/[id], /workflows/preview) in the simple pre-W7 shell.
// W7's customer-facing UI lives at /workspaces/[wsId]/... with a
// different sidebar (Health / Inbox / per-workflow nav / Library).
export default function LegacyLayout({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="nav">
        <div className="nav-brand">
          <svg className="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 1.75 L20.25 4.75 V12 C20.25 17 16.5 20.75 12 22.25 C7.5 20.75 3.75 17 3.75 12 V4.75 Z"
              fill="#3b82f6"
            />
            <circle cx="12" cy="12.5" r="3.2" stroke="#07090e" strokeWidth="2" />
            <path d="M9.6 7 L12 4.5 L14.4 7 Z" fill="#07090e" />
          </svg>
          <span className="brand-wordmark">
            <span className="logo-own">own</span>
            <span className="logo-evo">Evo</span>
          </span>
        </div>

        <div className="nav-section">Activity</div>
        <a href="/inbox" className="nav-item">
          <svg className="nav-icon" viewBox="0 0 16 16">
            <path d="M2 4 L2 12 A1.5 1.5 0 0 0 3.5 13.5 L12.5 13.5 A1.5 1.5 0 0 0 14 12 L14 4 M2 4 L8 9 L14 4 M2 4 L14 4" />
          </svg>
          <span className="nav-label">Inbox</span>
        </a>

        <div className="nav-section">Workflows</div>
        <a href="/workflows/preview" className="nav-item">
          <svg className="nav-icon" viewBox="0 0 16 16">
            <path d="M8 3 L8 13 M3 8 L13 8" />
          </svg>
          <span className="nav-label">New workflow</span>
        </a>

        <div className="nav-section">Workspace</div>
        <a href="/workspaces/acme" className="nav-item">
          <svg className="nav-icon" viewBox="0 0 16 16">
            <path d="M2 8 L5 8 L7 3 L9 13 L11 8 L14 8" />
          </svg>
          <span className="nav-label">Open workspace</span>
        </a>

        <div className="nav-footer">
          <ThemeToggle />
        </div>
      </aside>

      <main className="main">{children}</main>
    </div>
  )
}
