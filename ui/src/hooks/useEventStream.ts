import { useEffect, useRef, useState } from 'react'
import { openEventStream, type RunEvent } from '../api'

export function useEventStream(runId: string | null | undefined) {
  const [events, setEvents] = useState<RunEvent[]>([])
  const closeRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    setEvents([])
    if (!runId) {
      if (closeRef.current) closeRef.current()
      closeRef.current = null
      return
    }
    closeRef.current = openEventStream(runId, (evt) => {
      const ts = evt.ts || new Date().toISOString()
      setEvents((prev) => [...prev.slice(-399), { ...evt, ts }])
    })
    return () => {
      if (closeRef.current) closeRef.current()
      closeRef.current = null
    }
  }, [runId])

  return events
}
