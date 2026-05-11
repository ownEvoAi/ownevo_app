import type { KanbanData } from './types'

interface Props {
  data: KanbanData
}

const TAG_TONE_CLASS: Record<string, string> = {
  amber: 'pill amber',
  green: 'pill green',
  red: 'pill red',
  outline: 'pill outline',
}

export function KanbanBoard({ data }: Props) {
  return (
    <div className="kanban">
      {data.columns.map((col) => {
        const cards = data.cards.filter((c) => c.column_key === col.key)
        return (
          <div className="kanban-col" key={col.key}>
            <div className="kanban-col-header">
              <div className="kanban-col-title">{col.label}</div>
              <span className="kanban-col-count">{col.count}</span>
            </div>
            {cards.map((card) => (
              <div className="kanban-card" key={card.id}>
                <div className="kanban-card-title">{card.title}</div>
                <div className="kanban-card-body">{card.body}</div>
                <div className="kanban-card-meta">
                  <span>{card.meta}</span>
                  {card.tags && card.tags.length > 0 ? (
                    <div className="kanban-card-tags">
                      {card.tags.map((t, i) => (
                        <span
                          key={i}
                          className={TAG_TONE_CLASS[t.tone ?? 'outline']}
                        >
                          {t.label}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}
