import { useMemo } from 'react'
import { RunEvent } from '../api'

export function EventLog({ events }: { events: RunEvent[] }) {
  const items = useMemo(() => {
    return events
      .filter(Boolean)
      .map((evt, index) => {
        const label = typeof evt.event === 'string'
          ? evt.event
          : typeof evt.action === 'string'
            ? evt.action
            : typeof evt.level === 'string'
              ? evt.level
              : 'event'
        const message = typeof evt.message === 'string'
          ? evt.message
          : typeof evt.status === 'string'
            ? evt.status
            : ''
        return {
          key: `${evt.ts || index}-${index}`,
          ts: evt.ts ? new Date(evt.ts).toLocaleString() : '—',
          label,
          message,
          payload: evt.data,
        }
      })
  }, [events])

  if (!items.length) {
    return <div className="event-log__empty">No events yet.</div>
  }

  return (
    <div className="event-log">
      {items.map((item) => (
        <div className="event-log__item" key={item.key}>
          <div className="event-log__meta">
            <span className="event-log__label">{item.label}</span>
            <span className="event-log__timestamp">{item.ts}</span>
          </div>
          {item.message && <div className="event-log__message">{item.message}</div>}
          {item.payload !== undefined && item.payload !== null && (
            <pre className="event-log__payload">
              {JSON.stringify(item.payload, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  )
}
