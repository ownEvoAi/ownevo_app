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
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="light">
      <body>{children}</body>
    </html>
  )
}
