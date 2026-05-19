import Link from 'next/link'
import type { AlertItem } from './types'

interface Props {
  data: AlertItem[]
}

// Allow same-origin paths only; defensive against an upstream resolver
// putting an external URL in `action_url`. The Operate-context
// resolver always emits `/workspaces/.../traces/...`, so this is a
// belt-and-braces check.
function isInternalHref(href: string): boolean {
  return href.startsWith('/')
}

export function AlertList({ data }: Props) {
  return (
    <div className="alerts">
      {data.map((a, i) => {
        const inner = (
          <>
            <svg className="alert-icon" viewBox="0 0 16 16" aria-hidden>
              {a.severity === 'high' ? (
                <path d="M8 2 L14 13 L2 13 Z M8 6 L8 10 M8 12 L8 12.5" />
              ) : (
                <>
                  <circle cx="8" cy="8" r="6" />
                  <path d="M8 5 L8 9 M8 11 L8 11.5" />
                </>
              )}
            </svg>
            <div className="alert-body">
              <div className="alert-title">{a.title}</div>
              <div className="alert-meta">{a.meta}</div>
            </div>
          </>
        )
        if (a.action_url && isInternalHref(a.action_url)) {
          return (
            <Link
              key={i}
              href={a.action_url}
              className={`alert ${a.severity} alert-link`}
            >
              {inner}
            </Link>
          )
        }
        return (
          <div className={`alert ${a.severity}`} key={i}>
            {inner}
          </div>
        )
      })}
    </div>
  )
}
