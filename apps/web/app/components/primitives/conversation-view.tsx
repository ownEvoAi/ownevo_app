import type { ConversationData, ConvoMessage } from './types'

interface Props {
  data: ConversationData
}

function initials(author: string | undefined, role: ConvoMessage['role']): string {
  if (author) {
    const parts = author.trim().split(/\s+/)
    return (parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')
  }
  return role === 'agent' ? 'AI' : role === 'user' ? 'CS' : 'SY'
}

function authorLabel(m: ConvoMessage): string {
  if (m.author) return m.author
  return m.role === 'agent' ? 'Agent' : m.role === 'user' ? 'Customer' : 'System'
}

export function ConversationView({ data }: Props) {
  return (
    <div className="convo">
      {data.messages.map((m, i) => (
        <div className={`convo-msg ${m.role}`} key={i}>
          <div
            className={`convo-avatar ${m.role}`}
            aria-label={authorLabel(m)}
          >
            {initials(m.author, m.role)}
          </div>
          <div className="convo-bubble-wrap">
            <div className="convo-bubble">
              {m.text}
              {m.citations?.map((c) => (
                <span className="convo-citation" key={String(c.id)} title={c.source}>
                  {c.id}
                </span>
              ))}
            </div>
            <div className="convo-meta">
              {authorLabel(m)}
              {m.ts ? ` · ${m.ts}` : ''}
              {m.citations && m.citations.length > 0
                ? ` · ${m.citations.length} citation${m.citations.length === 1 ? '' : 's'}`
                : ''}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
