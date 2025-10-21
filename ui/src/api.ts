import { LoginRequest, LoginResponse, Session } from './types/session'
import { Experiment, ExperimentListResponse } from './types/experiment'
import { AuditEvent, AuditEventsResponse } from './types/audit'
import { AnalyticsSummary, HealthCheck } from './types/analytics'

export const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000'

// Session token storage
let sessionToken: string | null = localStorage.getItem('lizard_session_token')

export function setSessionToken(token: string) {
  sessionToken = token
  localStorage.setItem('lizard_session_token', token)
}

export function clearSessionToken() {
  sessionToken = null
  localStorage.removeItem('lizard_session_token')
}

export function getSessionToken(): string | null {
  return sessionToken
}

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
  user_id?: string | null
  username?: string | null
  session_id?: string | null
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
  const headers = new Headers(init?.headers || {})
  
  // Add session token if available
  if (sessionToken) {
    headers.set('X-Session-Token', sessionToken)
  }
  
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  })
  
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
    return await request<RunResults>(`/runs/${id}/artifacts/results.json`)
  } catch (error) {
    return null
  }
}

export async function getRunSummary(id: string): Promise<string | null> {
  try {
    return await request<string>(`/runs/${id}/artifacts/summary.txt`, undefined, 'text')
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


// ==================== Session Management ====================

export async function login(req: LoginRequest): Promise<LoginResponse> {
  const response = await request<LoginResponse>('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  setSessionToken(response.token)
  return response
}

export async function logout(): Promise<void> {
  await request('/auth/logout', { method: 'POST' })
  clearSessionToken()
}

export async function getCurrentSession(): Promise<Session> {
  return request<Session>('/auth/session')
}

export async function listUserSessions(): Promise<{ sessions: Session[] }> {
  return request<{ sessions: Session[] }>('/auth/sessions')
}


// ==================== Experiment Management ====================

export async function getExperiment(experimentId: string): Promise<Experiment> {
  return request<Experiment>(`/experiments/${experimentId}`)
}

export async function listExperiments(status?: string, limit = 50): Promise<Experiment[]> {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  params.set('limit', limit.toString())
  
  const data = await request<ExperimentListResponse>(`/experiments?${params}`)
  return data.experiments
}

export async function getExperimentByRun(runId: string): Promise<Experiment> {
  return request<Experiment>(`/experiments/run/${runId}`)
}

export async function getFailedExperiments(limit = 20): Promise<Experiment[]> {
  const data = await request<ExperimentListResponse>(`/experiments/failed?limit=${limit}`)
  return data.experiments
}


// ==================== Audit Logging ====================

export async function getAuditEvents(
  startDate?: string,
  endDate?: string,
  eventType?: string,
  limit = 100
): Promise<AuditEvent[]> {
  const params = new URLSearchParams()
  if (startDate) params.set('start_date', startDate)
  if (endDate) params.set('end_date', endDate)
  if (eventType) params.set('event_type', eventType)
  params.set('limit', limit.toString())
  
  const data = await request<AuditEventsResponse>(`/audit/events?${params}`)
  return data.events
}

export async function getUserActivity(limit = 50): Promise<AuditEvent[]> {
  const data = await request<AuditEventsResponse>(`/audit/activity?limit=${limit}`)
  return data.events
}

export async function getSecurityEvents(limit = 50): Promise<AuditEvent[]> {
  const data = await request<AuditEventsResponse>(`/audit/security?limit=${limit}`)
  return data.events
}


// ==================== Analytics ====================

export async function getAnalyticsSummary(): Promise<AnalyticsSummary> {
  return request<AnalyticsSummary>('/analytics/summary')
}

export async function getHealthCheck(): Promise<HealthCheck> {
  return request<HealthCheck>('/health')
}

