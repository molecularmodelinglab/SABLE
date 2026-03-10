import {
  AuthProfile,
  AuthUser,
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  RegisterResponse,
  Session,
  SessionListResponse,
  PasswordResetInitiateRequest,
  PasswordResetInitiateResponse,
  PasswordResetConfirmRequest,
  ApiMessageResponse,
} from './types/session'
import {
  ConversationConfirmRequest,
  ConversationCreateRunResponse,
  ConversationMessageRequest,
  ConversationResponse,
  ConversationStartRequest,
} from './types/conversation'
import { Experiment, ExperimentListResponse } from './types/experiment'
import { AuditEvent, AuditEventsResponse } from './types/audit'
import { AnalyticsSummary, HealthCheck } from './types/analytics'
import { AdminAnalyticsSummary } from './types/admin'

export const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000'

// Access token storage
let accessToken: string | null = localStorage.getItem('lizard_access_token')

export function setAccessToken(token: string) {
  accessToken = token
  localStorage.setItem('lizard_access_token', token)
}

export function clearAccessToken() {
  accessToken = null
  localStorage.removeItem('lizard_access_token')
}

export function getAccessToken(): string | null {
  return accessToken
}

export type RunInfo = {
  id: string
  status: string
  created_at: string
  updated_at: string
  prompt?: string | null
  exit_reason?: string | null
  summary_available: boolean
  results_available: boolean
  paths?: Record<string, string>
  note?: string | null
  user_id?: string | null
  username?: string | null
  session_id?: string | null
  starting_molecules?: string[]
  run_id?: string
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

export type RunPlotArtifact = {
  name: string
  path: string
  size_bytes: number
}

export type GenerateRunPlotsResponse = {
  generated: boolean
  workflow_id?: string
  checkpoint: string
  output_dir: string
  plot_count: number
}

async function request<T>(path: string, init?: RequestInit, parse: 'json' | 'text' = 'json'): Promise<T> {
  const headers = new Headers(init?.headers || {})
  
  // Add authorization token if available
  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`)
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

// ==================== Admin Run Inspection ====================

export async function listAdminRuns(limit = 100, offset = 0): Promise<RunInfo[]> {
  const params = new URLSearchParams()
  params.set('limit', limit.toString())
  params.set('offset', offset.toString())
  const data = await request<{ runs: RunInfo[] }>(`/admin/runs?${params}`)
  return data.runs
}

export async function getAdminRun(id: string): Promise<RunInfo> {
  return request<RunInfo>(`/admin/runs/${id}`)
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

export async function listRunPlots(id: string): Promise<RunPlotArtifact[]> {
  try {
    const data = await request<{ plots: RunPlotArtifact[] }>(`/runs/${id}/artifacts/plots`)
    return data.plots
  } catch (error) {
    return []
  }
}

export function getRunPlotUrl(id: string, plotPath: string): string {
  const base = API_BASE.replace(/\/$/, '')
  const encodedPath = plotPath
    .split('/')
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join('/')

  const url = new URL(`${base}/runs/${id}/artifacts/plots/${encodedPath}`)
  const token = getAccessToken()
  if (token) {
    url.searchParams.set('access_token', token)
  }
  return url.toString()
}

export async function generateRunPlots(id: string): Promise<GenerateRunPlotsResponse> {
  return request<GenerateRunPlotsResponse>(`/runs/${id}/artifacts/plots/generate`, {
    method: 'POST',
  })
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
  const base = API_BASE.replace(/\/$/, '')
  const url = new URL(`${base}/runs/${id}/events`)
  const token = getAccessToken()
  if (token) {
    url.searchParams.set('access_token', token)
  }
  const es = new EventSource(url.toString())
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
  setAccessToken(response.access_token)
  return response
}

export async function registerUser(req: RegisterRequest): Promise<RegisterResponse> {
  const response = await request<RegisterResponse>('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  return response
}

export async function logout(): Promise<void> {
  await request('/auth/logout', { method: 'POST' })
  clearAccessToken()
}

export async function requestPasswordReset(
  req: PasswordResetInitiateRequest
): Promise<PasswordResetInitiateResponse> {
  return request<PasswordResetInitiateResponse>('/auth/forgot-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

export async function confirmPasswordReset(req: PasswordResetConfirmRequest): Promise<ApiMessageResponse> {
  const response = await request<ApiMessageResponse>('/auth/reset-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  clearAccessToken()
  return response
}

// ==================== Conversation Assistant ====================

export async function startConversation(req?: ConversationStartRequest): Promise<ConversationResponse> {
  return request<ConversationResponse>('/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req ?? {}),
  })
}

export async function sendConversationMessage(
  conversationId: string,
  req: ConversationMessageRequest
): Promise<ConversationResponse> {
  return request<ConversationResponse>(`/conversations/${conversationId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

export async function confirmConversation(
  conversationId: string,
  req: ConversationConfirmRequest
): Promise<ConversationCreateRunResponse> {
  return request<ConversationCreateRunResponse>(`/conversations/${conversationId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

export async function abandonConversation(conversationId: string): Promise<{ message: string }> {
  return request<{ message: string }>(`/conversations/${conversationId}`, {
    method: 'DELETE',
  })
}

export async function getCurrentUser(): Promise<AuthUser> {
  return request<AuthUser>('/auth/me')
}

export async function listUserSessions(): Promise<SessionListResponse> {
  return request<SessionListResponse>('/auth/sessions')
}

export async function getAuthProfile(): Promise<AuthProfile> {
  const [user, sessionList] = await Promise.all([
    getCurrentUser(),
    listUserSessions().catch(() => ({ sessions: [], total: 0 } as SessionListResponse)),
  ])

  return {
    user,
    session: sessionList.sessions[0] ?? null,
  }
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


// ==================== Admin Analytics ====================

export async function getAdminAnalyticsSummary(): Promise<AdminAnalyticsSummary> {
  return request<AdminAnalyticsSummary>('/admin/analytics/summary')
}

