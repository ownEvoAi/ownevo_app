import type { Metadata, Viewport } from 'next'
import type { ReactNode } from 'react'
import './globals.css'

export const metadata: Metadata = {
  title: 'ownEvo',
  description: 'ownEvo workspace.',
  robots: { index: false, follow: false },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

// Bare root layout — owns <html>/<body>/global CSS only. Each subtree
// renders its own shell:
//   - app/(legacy)/      — flat W2.5/W5.5 routes (/inbox, /proposals,
//                          /workflows/preview); simple sidebar
//   - app/workspaces/[wsId]/  — W7 customer-facing workspace shell.
// Inline theme bootstrap. Runs synchronously in <head> before any
// paint, so dark-mode users don't get a white flash on every
// navigation. Reads the same `ownevo-theme` localStorage key the
// ThemeToggle writes; falls through to the SSR'd `data-theme="light"`
// default when nothing is stored. The `try/catch` keeps Safari
// private-mode (no localStorage access) from breaking rendering.
const THEME_BOOTSTRAP = `
(function () {
  try {
    var t = localStorage.getItem('ownevo-theme');
    if (t === 'dark' || t === 'light') {
      document.documentElement.setAttribute('data-theme', t);
    }
  } catch (e) {}
})();
`

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>{children}</body>
    </html>
  )
}
