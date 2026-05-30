import Link from 'next/link'
import type { ViewCaseCaption } from './types'

// Footer link the Operate-context resolver attaches to single-source
// views (TimeSeriesChart, SideBySideView, DocumentReader,
// ConversationView, ScheduleGrid) so the operator can jump from the
// rendered artifact into the agent's full trace for the case that
// produced it. Multi-row views embed the link per row/item
// instead of using this footer.
interface Props {
 caption: ViewCaseCaption | undefined
}

function isInternalHref(href: string): boolean {
 return href.startsWith('/')
}

export function CaseCaption({ caption }: Props) {
 if (!caption || !isInternalHref(caption.href)) return null
 return (
 <div
 style={{
 marginTop: 10,
 fontSize: 12,
 color: 'var(--text-muted)',
 textAlign: 'right',
 }}
 >
 <Link href={caption.href} style={{ color: 'var(--accent)' }}>
 {caption.text}
 </Link>
 </div>
 )
}
