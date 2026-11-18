import { useEffect, useRef, useState } from 'react'
import { API_BASE, getAccessToken } from '../api'
import { useAuthProfile } from '../hooks/useAuthProfile'

type Observation = { input_smiles: string; canonical_smiles: string; method: string; method_version: string; values: { property: string; value: number; unit: string }[] }
type ScientificResult = { observations?: unknown; kind?: string; candidates?: { id: string; smiles: string }[]; round?: number; stop_reason?: string | null; evaluated_candidates?: number; library_size?: number; constraints?: unknown; exclusions?: unknown[]; rankings?: { molecule_id: string; smiles: string; objective_values: Record<string, number>; rank: number | null; pareto_front: number | null }[]; [key: string]: unknown }
type JobStatus = 'queued' | 'submitting' | 'unknown' | 'running' | 'succeeded' | 'partial' | 'failed' | 'cancelled' | 'timed_out' | 'cancelling'
type Job = { id: string; campaign: string; action: string; skill_id: string; remote_id: string | null; status: JobStatus; can_cancel: boolean; can_reconcile: boolean; created: string | number; updated: string | number; next_poll: string | number | null; deadline: string | number | null; result?: unknown; error?: unknown; diagnostics?: Record<string, unknown> }
type Artifact = { id: string; name: string; size_bytes: number; sha256: string }
type Campaign = { id: string; status: string; busy: boolean; question: string | null; error: string | null; turns: number; max_turns: number; report: { summary: string; evidence: string[] } | null; results: Record<string, ScientificResult>; jobs?: Job[]; artifacts?: Artifact[]; skills?: { id: string; version: string; digest: string }[]; model?: string; metrics?: { tool_actions: number; max_tool_actions: number; operation_units: number; max_operation_units: number; input_tokens: number; output_tokens: number } }
type Capability = { id: string; version: string; description: string; digest: string; available: boolean; resumable: boolean; requirements: unknown; operations: { id: string; description: string }[] }
type Event = { event_id: string; action: string; sequence: number; timestamp: string; kind: string; payload: unknown; preview?: boolean }

const activeJob = (job: Job) => ['queued', 'submitting', 'running', 'cancelling'].includes(job.status)
const timestamp = (value: string | number | null) => value === null ? NaN : typeof value === 'number' ? value * 1000 : Date.parse(value)
const jobTime = (value: string | number | null) => Number.isFinite(timestamp(value)) ? new Date(timestamp(value)).toLocaleString() : 'Not scheduled'
const detail = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
function descriptorObservations(value: unknown): value is Observation[] {
  return Array.isArray(value) && value.length > 0 && value.every(observation => record(observation) &&
    typeof observation.input_smiles === 'string' && typeof observation.canonical_smiles === 'string' &&
    typeof observation.method === 'string' && typeof observation.method_version === 'string' &&
    Array.isArray(observation.values) && observation.values.every(value => record(value) &&
      typeof value.property === 'string' && typeof value.value === 'number' && Number.isFinite(value.value) && typeof value.unit === 'string'))
}

function download(name: string, value: unknown, plain = false) {
  const blob = new Blob([plain ? String(value) : JSON.stringify(value, null, 2)], { type: plain ? 'text/plain;charset=utf-8' : 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name
  anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
const eventGroup = (kind: string) => kind.startsWith('model_') ? 'model' : kind.startsWith('action_') ? 'tool' : kind.startsWith('job_') ? 'job' : kind.startsWith('artifact_') ? 'file' : 'campaign'

async function request<T>(path = '', body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}/v2/campaigns${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${getAccessToken() || ''}`, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail ? detail(data.detail) : `Request failed (${response.status})`)
  }
  return response.json()
}

export function AgentCampaignPage() {
  const { data: profile } = useAuthProfile()
  const isAdmin = Boolean(profile?.user.roles?.includes('admin'))
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [campaign, setCampaign] = useState<Campaign | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [traceFilter, setTraceFilter] = useState('all')
  const [traceSearch, setTraceSearch] = useState('')
  const [fullEvent, setFullEvent] = useState<Event | null>(null)
  const [prompt, setPrompt] = useState('Calculate QED and molecular weight for aspirin: CC(=O)Oc1ccccc1C(=O)O')
  const [reply, setReply] = useState('')
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [jobError, setJobError] = useState('')
  const [jobWorking, setJobWorking] = useState(false)
  const [remoteIds, setRemoteIds] = useState<Record<string, string>>({})
  const requestBusy = useRef(false)
  const retryAfter = useRef(0)
  const lastPoll = useRef<Record<string, number>>({})
  const controlsBusy = working || jobWorking
  const canUpload = !!campaign && !campaign.busy && ['ready', 'awaiting_user', 'waiting', 'waiting_jobs'].includes(campaign.status)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; request<Campaign[]>().then(setCampaigns).catch(e => setError(e.message)); return () => { mounted.current = false } }, [])
  useEffect(() => { request<Capability[]>('/capabilities').then(data => { if (Array.isArray(data)) setCapabilities(data) }).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    if (!campaign) return
    const id = campaign.id
    let active = true, fetching = false, cursor = 0
    setEvents([]); setFullEvent(null); setTraceFilter('all'); setTraceSearch('')
    const refresh = async () => {
      if (fetching) return
      fetching = true
      try {
        const data = await request<Event[]>(`/${id}/events?after=${cursor}&limit=200&preview=true`)
        if (active && Array.isArray(data) && data.length) {
          cursor = data[data.length - 1].sequence
          setEvents(previous => [...previous, ...data.filter(event => !previous.some(old => old.event_id === event.event_id))].slice(-1000))
        }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : 'Activity refresh failed') }
      finally { fetching = false }
    }
    void refresh()
    const timer = window.setInterval(refresh, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [campaign?.id])

  useEffect(() => {
    setJobError(''); setRemoteIds({}); retryAfter.current = 0
  }, [campaign?.id])

  useEffect(() => {
    if (!campaign || campaign.status !== 'waiting_jobs' || working) return
    const id = campaign.id
    let active = true
    let timer: number
    const tick = async () => {
      if (!active) return
      if (!requestBusy.current && Date.now() >= retryAfter.current) {
        requestBusy.current = true
        setJobWorking(true)
        try {
          // Refresh first: another client may have reconciled or completed a job.
          let next = await request<Campaign>(`/${encodeURIComponent(id)}`)
          if (!active) return
          if (next.status === 'waiting_jobs' && !next.busy) {
            const due = (next.jobs || []).filter(job => activeJob(job) &&
              (job.next_poll === null || timestamp(job.next_poll) <= Date.now()))
              .sort((a, b) => (lastPoll.current[a.id] || 0) - (lastPoll.current[b.id] || 0))[0]
            if (due) {
              lastPoll.current[due.id] = Date.now()
              // Polling never advances the model or retries an unknown submission.
              next = await request<Campaign>(`/${encodeURIComponent(id)}/jobs/${encodeURIComponent(due.id)}/poll`, {})
            }
          }
          if (active) { setCampaign(next); setJobError('') }
        } catch (e) {
          retryAfter.current = Date.now() + 30000
          if (active) setJobError(e instanceof Error ? e.message : 'Job refresh failed')
        } finally {
          requestBusy.current = false
          if (mounted.current) setJobWorking(false)
        }
      }
      if (active) timer = window.setTimeout(tick, 5000)
    }
    timer = window.setTimeout(tick, 5000)
    return () => { active = false; window.clearTimeout(timer) }
  }, [campaign, working])

  async function controlJob(job: Job, action: 'poll' | 'cancel' | 'reconcile') {
    if (!campaign || requestBusy.current) return
    if (action === 'cancel' && !job.can_cancel) return
    if (action === 'reconcile' && (!isAdmin || !job.can_reconcile)) return
    requestBusy.current = true
    setJobWorking(true); setJobError('')
    const path = `/${encodeURIComponent(campaign.id)}/jobs/${encodeURIComponent(job.id)}`
    try {
      const next = await request<Campaign>(`${path}/${action}`, action === 'reconcile' ? { remote_id: (remoteIds[job.id] || '').trim() } : {})
      if (mounted.current) { setCampaign(next); retryAfter.current = 0 }
    } catch (e) {
      retryAfter.current = Date.now() + 30000
      if (mounted.current) setJobError(`${job.id}: ${e instanceof Error ? e.message : 'Job request failed'}`)
    } finally {
      requestBusy.current = false
      if (mounted.current) setJobWorking(false)
    }
  }

  async function run(current: Campaign) {
    // One durable model decision per request; a refresh can resume between steps.
    setCampaign(current)
    while (mounted.current && current.status === 'ready' && !current.busy) {
      current = await request<Campaign>(`/${current.id}/advance`, {})
      if (mounted.current) setCampaign(current)
    }
    if (mounted.current) {
      setCampaigns(await request<Campaign[]>())
    }
  }
  async function perform(action: () => Promise<void>) {
    if (requestBusy.current) return
    requestBusy.current = true
    setWorking(true); setError('')
    try { await action() } catch (e) { setError(e instanceof Error ? e.message : 'Campaign request failed') }
    finally { requestBusy.current = false; if (mounted.current) setWorking(false) }
  }

  async function uploadArtifact(file: File) {
    if (!campaign || !canUpload) return
    const id = campaign.id
    await perform(async () => {
      const maxBytes = 3_000_000
      if (file.size > maxBytes) throw new Error('Choose a coordinate or text file no larger than 3 MB (3,000,000 bytes).')
      const content = await file.text()
      if (new TextEncoder().encode(content).byteLength > maxBytes) throw new Error('File text exceeds the 3 MB upload limit.')
      if (!mounted.current) return
      const next = await request<Campaign>(`/${encodeURIComponent(id)}/artifacts`, { name: file.name, content })
      if (mounted.current) setCampaign(next)
    })
  }

  return <section className="agent-campaign">
    <h1>Scientific agent campaigns</h1>
    <details><summary>Available capabilities ({capabilities.filter(c => c.available).length})</summary>
      {capabilities.map(c => <article key={`${c.id}@${c.version}`}><h3>{c.id} · {c.version}</h3>
        <p>{c.description}</p><p>{c.available ? 'Available for new campaigns' : c.resumable ? 'Retained for existing campaigns' : 'Unavailable: check deployment requirements'}</p>
        <ul>{c.operations.map(op => <li key={op.id}>{op.id}: {op.description}</li>)}</ul>
        <details><summary>Requirements and package identity</summary><pre>{JSON.stringify({ requirements: c.requirements, digest: c.digest }, null, 2)}</pre></details>
      </article>)}
    </details>
    <p>Describe your objective, molecular structures, constraints, and evaluation budget. With the available skills, the agent can explore analogues, calculate descriptors, choose evaluation batches, and report observed results.</p>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={e => { e.preventDefault(); perform(async () => run(await request<Campaign>('', { message: prompt }))) }}>
      <label htmlFor="agent-prompt">New campaign</label><br />
      <textarea id="agent-prompt" value={prompt} onChange={e => setPrompt(e.target.value)} required maxLength={10000} rows={4} style={{ width: '100%' }} disabled={controlsBusy} />
      <button disabled={controlsBusy || !prompt.trim()} type="submit">Start campaign</button>
      <button disabled={controlsBusy || !prompt.trim()} type="button" onClick={() => perform(async () => {
        const next = await request<Campaign>('', { message: prompt })
        if (mounted.current) { setEvents([]); setCampaign(next) }
        const history = await request<Campaign[]>()
        if (mounted.current) setCampaigns(history)
      })}>Create campaign to add files</button>
    </form>
    <label htmlFor="agent-history">Previous campaigns </label>
    <select id="agent-history" value={campaign?.id || ''} disabled={controlsBusy} onChange={e => {
      const id = e.target.value
      if (id) perform(async () => { setEvents([]); setCampaign(await request<Campaign>(`/${id}`)) })
    }}><option value="">Select a campaign</option>{campaigns.map(c => <option key={c.id} value={c.id}>{c.id} — {c.status}</option>)}</select>
    {campaign && <>
      <h2>Campaign activity</h2>
      {campaign.metrics && <p>Tool actions: {campaign.metrics.tool_actions}/{campaign.metrics.max_tool_actions} · Operation units: {campaign.metrics.operation_units}/{campaign.metrics.max_operation_units} · Tokens: {campaign.metrics.input_tokens.toLocaleString()} input / {campaign.metrics.output_tokens.toLocaleString()} output</p>}
      <details><summary>Model and pinned skills</summary><p>{campaign.model || 'No model decision yet'}</p><ul>{campaign.skills?.map(skill => <li key={skill.id}>{skill.id} {skill.version}<br /><code>{skill.digest}</code></li>)}</ul></details>
      <button disabled={controlsBusy} onClick={() => perform(async () => download(`${campaign.id}.json`, await request(`/${campaign.id}/export`)))}>Download campaign record</button>
      <p>The record contains user and tool text. Review it before sharing or dataset use.</p>
      <p role="status">{campaign.busy || working ? 'Working' : campaign.status.replace(/_/g, ' ')} · {campaign.turns}/{campaign.max_turns} decisions{jobWorking ? ' · Updating remote jobs' : ''}</p>
      {campaign.error && <p role="alert">The agent step failed: {campaign.error}. Review the recorded activity; no automatic retry was submitted.</p>}
      <div>
        <h2>Campaign files</h2>
        <p>Upload OpenFE coordinate files as PDB, SDF, or text, one file at a time (up to 3 MB). The agent receives file names and artifact IDs automatically.</p>
        <label htmlFor="campaign-artifact">Choose a file to upload </label>
        <input id="campaign-artifact" type="file" accept=".pdb,.sdf,.txt,text/plain" disabled={controlsBusy || !canUpload} onChange={e => {
          const file = e.currentTarget.files?.[0]
          e.currentTarget.value = ''
          if (file) void uploadArtifact(file)
        }} />
        {campaign.artifacts?.length ? <ul>{campaign.artifacts.map(artifact => <li key={artifact.id}>
          {artifact.name} · Artifact ID: <code>{artifact.id}</code> · {artifact.size_bytes.toLocaleString()} bytes
          <button disabled={controlsBusy} onClick={() => perform(async () => { const value = await request<{ content: string }>(`/${campaign.id}/artifacts/${artifact.id}`); download(artifact.name, value.content, true) })}>Download file</button>
          <details><summary>SHA-256</summary><code style={{ overflowWrap: 'anywhere' }}>{artifact.sha256}</code></details>
        </li>)}</ul> : <p>No files uploaded yet.</p>}
      </div>
      {(campaign.status === 'waiting_jobs' || !!campaign.jobs?.length) && <div>
        <h2>Remote jobs</h2>
        {campaign.status === 'waiting_jobs' && <p>Waiting for remote work. Due active jobs are checked one at a time, at most once every 5 seconds, with a 30-second pause after errors. These checks do not consume model decisions. When ready, use Continue campaign to resume.</p>}
        <p>Cancellation requests may be unsupported or may remain pending. A timeout does not confirm that remote work stopped.</p>
        {jobError && <p role="alert">{jobError}</p>}
        {!campaign.jobs?.length && <p>No remote jobs recorded yet.</p>}
        {(campaign.jobs || []).map(job => <article key={job.id} aria-label={`Remote job ${job.id}`}>
          <h3>{job.skill_id} · {job.action}</h3>
          <p>Job {job.id} · <strong>{job.status.replace(/_/g, ' ')}</strong></p>
          <p>Remote ID: {job.remote_id || 'Not linked'}</p>
          <p>Created: {jobTime(job.created)} · Updated: {jobTime(job.updated)}<br />Next poll: {jobTime(job.next_poll)} · Deadline: {jobTime(job.deadline)}</p>
          {job.status === 'unknown' && <p>The submission outcome is unknown. It will not be automatically resubmitted. {job.can_reconcile
            ? <>An administrator must verify that the provider job belongs to this campaign's owner before linking its remote ID. Shared service credentials do not establish job ownership.{!isAdmin && ' Contact an administrator to reconcile this submission.'}</>
            : 'The provider has no safe status lookup. Operator review is required.'}</p>}
          {job.status === 'partial' && <p>Only partial results were returned; this job did not fully succeed.</p>}
          {job.status === 'failed' && <p role="alert">Remote job failed. Review the error and any recorded results.</p>}
          {job.status === 'cancelling' && <p>Cancellation requested; remote work is not yet confirmed stopped.</p>}
          {job.status === 'cancelled' && <p>This job is recorded as cancelled; see trace for submission details.</p>}
          {job.status === 'timed_out' && <p role="alert">Tracking timed out. Remote work may still be running; check the remote service.</p>}
          {job.error != null && <pre role="alert" style={{ whiteSpace: 'pre-wrap' }}>{detail(job.error)}</pre>}
          {job.diagnostics != null && <details><summary>Job diagnostics</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(job.diagnostics, null, 2)}</pre></details>}
          {job.result != null && <details><summary>{job.status === 'partial' ? 'Partial job result' : 'Recorded job result'}</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{detail(job.result)}</pre></details>}
          {(activeJob(job) || job.status === 'timed_out') && <button disabled={controlsBusy || campaign.busy} onClick={() => controlJob(job, 'poll')}>Poll job now</button>}
          {job.can_cancel && (activeJob(job) || job.status === 'unknown' || job.status === 'timed_out') && job.status !== 'cancelling' && <button disabled={controlsBusy || campaign.busy} onClick={() => controlJob(job, 'cancel')}>Request cancellation</button>}
          {job.status === 'unknown' && job.can_reconcile && isAdmin && <form onSubmit={e => { e.preventDefault(); if (remoteIds[job.id]?.trim()) void controlJob(job, 'reconcile') }}>
            <label htmlFor={`remote-id-${job.id}`}>Existing remote job ID</label>{' '}
            <input id={`remote-id-${job.id}`} required maxLength={512} value={remoteIds[job.id] || ''} disabled={controlsBusy || campaign.busy} onChange={e => setRemoteIds(ids => ({ ...ids, [job.id]: e.target.value }))} />
            <button type="submit" disabled={controlsBusy || campaign.busy || !remoteIds[job.id]?.trim()}>Link remote job</button>
          </form>}
        </article>)}
      </div>}
      {campaign.status === 'ready' && <button disabled={controlsBusy || campaign.busy} onClick={() => perform(() => run(campaign))}>Continue campaign</button>}
      {campaign.question && <form onSubmit={e => { e.preventDefault(); perform(async () => {
        const next = await request<Campaign>(`/${campaign.id}/messages`, { message: reply }); setReply(''); await run(next)
      }) }}>
        <p>{campaign.question}</p><label htmlFor="agent-reply">Your answer</label><br />
        <textarea id="agent-reply" required maxLength={10000} value={reply} onChange={e => setReply(e.target.value)} disabled={controlsBusy} />
        <button disabled={controlsBusy || !reply.trim()} type="submit">Send answer</button>
      </form>}
      {campaign.report && <div><h2>Report</h2><p style={{ whiteSpace: 'pre-wrap' }}>{campaign.report.summary}</p>
        <p>Evidence: {campaign.report.evidence.map(id => <a key={id} href={`#result-${id}`} style={{ marginRight: 12 }}>{id}</a>)}</p></div>}
      {Object.entries(campaign.results).map(([id, result]) => <div id={`result-${id}`} key={id}>
        <h3>Result {id}</h3>
        {result.observations !== undefined ? descriptorObservations(result.observations) ? result.observations.map((observation, index) => <div key={index}>
          <p>{observation.canonical_smiles} · {observation.method} {observation.method_version} · computed</p>
          <table><thead><tr><th>Property</th><th>Value</th><th>Unit</th></tr></thead><tbody>{observation.values.map(value => <tr key={value.property}><td>{value.property}</td><td>{value.value.toPrecision(6)}</td><td>{value.unit}</td></tr>)}</tbody></table>
        </div>) : <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(result, null, 2)}</pre> : result.candidates ? <>
          <p>{result.kind === 'recommendation' ? `Round ${result.round}: ${result.candidates.length} candidates recommended` : `${result.candidates.length} eligible candidates; ${result.exclusions?.length || 0} excluded`}</p>
          {result.stop_reason && <p>Stopped: {result.stop_reason.replace(/_/g, ' ')}</p>}
          {result.constraints !== undefined && <details><summary>Enforced constraints</summary><pre>{JSON.stringify(result.constraints, null, 2)}</pre></details>}
          <table><thead><tr><th>Candidate</th><th>SMILES</th></tr></thead><tbody>{result.candidates.map(candidate => <tr key={candidate.id}><td>{candidate.id}</td><td>{candidate.smiles}</td></tr>)}</tbody></table>
        </> : result.rankings ? <>
          <p>{result.evaluated_candidates} of {result.library_size} library candidates evaluated. Rankings describe measured computational objectives.</p>
          <table><thead><tr><th>Rank</th><th>Pareto front</th><th>SMILES</th><th>Observed objectives</th></tr></thead><tbody>{result.rankings.map(row => <tr key={row.molecule_id}><td>{row.rank ?? 'Unranked'}</td><td>{row.pareto_front ?? '—'}</td><td>{row.smiles}</td><td>{Object.entries(row.objective_values).map(([name, value]) => `${name}: ${value.toPrecision(6)}`).join('; ')}</td></tr>)}</tbody></table>
        </> : <pre>{JSON.stringify(result, null, 2)}</pre>}
      </div>)}
      <h2>Activity timeline</h2>
      <label htmlFor="trace-category">Category </label><select id="trace-category" value={traceFilter} onChange={e => setTraceFilter(e.target.value)}>
        {['all', 'model', 'tool', 'job', 'file', 'campaign'].map(group => <option key={group} value={group}>{group}</option>)}
      </select>{' '}
      <label htmlFor="trace-search">Search activity </label><input id="trace-search" value={traceSearch} onChange={e => setTraceSearch(e.target.value)} />
      <p>Showing up to 1,000 recent events with bounded previews. Download the campaign record for the full timeline.</p>
      {events.filter(event => (traceFilter === 'all' || eventGroup(event.kind) === traceFilter) && `${event.kind} ${event.action} ${JSON.stringify(event.payload)}`.toLowerCase().includes(traceSearch.toLowerCase())).map(event =>
        <details key={event.event_id}><summary>{new Date(event.timestamp).toLocaleTimeString()} · {eventGroup(event.kind)} · {event.kind.replace(/_/g, ' ')} · {event.action}</summary>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(event.payload, null, 2)}</pre>
          <button disabled={controlsBusy} onClick={() => perform(async () => setFullEvent(await request<Event>(`/${campaign.id}/events/${event.sequence}`)))}>Inspect full event {event.sequence}</button>
          {campaign.results[event.action] && <a href={`#result-${event.action}`}>View result</a>}
        </details>)}
      {fullEvent && <section aria-label="Full event"><h3>{fullEvent.kind} · {fullEvent.action}</h3><button onClick={() => setFullEvent(null)}>Close event</button><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(fullEvent.payload, null, 2)}</pre></section>}
    </>}
  </section>
}
