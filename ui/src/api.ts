export const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000'

export type RunInfo = {
  id: string
  status: string
  created_at: string
  updated_at: string
  exit_reason?: string | null
  summary_available: boolean
  results_available: boolean
  paths?: Record<string, string>
  note?: string | null
}

export type RunEvent = {
  ts?: string
  event?: string
  action?: string
  level?: string
  message?: string
  data?: unknown
  [key: string]: unknown
}

export type RunResults = unknown

async function request<T>(path: string, init?: RequestInit, parse: 'json' | 'text' = 'json'): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) {
    const body = await res.text()
    throw new Error(body || res.statusText)
  }
  if (parse === 'text') {
    return res.text() as unknown as T
  }
  return res.json() as Promise<T>
}

export async function createRun(prompt: string, max_iterations?: number, batch_size?: number, note?: string): Promise<RunInfo> {
  return request<RunInfo>('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, max_iterations, batch_size, note }),
  })
}

export async function listRuns(): Promise<RunInfo[]> {
  const data = await request<{ runs: RunInfo[] }>('/runs')
  return data.runs
}

export async function getRun(id: string): Promise<RunInfo> {
  return request<RunInfo>(`/runs/${id}`)
}

export async function getCheckpoints(id: string): Promise<string[]> {
  return request<string[]>(`/runs/${id}/checkpoints`)
}

export async function getRunResults(id: string): Promise<RunResults | null> {
  try {
    const res = await fetch(`${API_BASE}/runs/${id}/artifacts/results.json`)
    if (!res.ok) {
      return null
    }
    return (await res.json()) as RunResults
  } catch (error) {
    return null
  }
}

export async function getRunSummary(id: string): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/runs/${id}/artifacts/summary.txt`)
    if (!res.ok) {
      return null
    }
    return await res.text()
  } catch (error) {
    return null
  }
}

export async function getRunLogs(id: string, limit = 500): Promise<RunEvent[]> {
  try {
    const data = await request<{ events: RunEvent[] }>(`/runs/${id}/logs?limit=${limit}`)
    return data.events
  } catch (error) {
    return []
  }
}

export function openEventStream(id: string, onEvent: (evt: RunEvent) => void): () => void {
  const url = `${API_BASE}/runs/${id}/events`
  const es = new EventSource(url)
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as RunEvent
      onEvent(data)
    } catch {}
  }
  es.onerror = () => {
    /* browser retries automatically */
  }
  return () => es.close()
}

export async function deleteRun(id: string): Promise<void> {
  await request(`/runs/${id}`, { method: 'DELETE' })
}
