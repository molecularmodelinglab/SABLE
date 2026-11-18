import { useEffect, useRef, useState } from 'react'
import { API_BASE, getAccessToken } from '../api'

type Observation = { input_smiles: string; canonical_smiles: string; method: string; method_version: string; values: { property: string; value: number; unit: string }[] }
type Campaign = { id: string; status: string; busy: boolean; question: string | null; error: string | null; turns: number; max_turns: number; report: { summary: string; evidence: string[] } | null; results: Record<string, { observations?: Observation[]; [key: string]: unknown }> }
type Event = { event_id: string; action: string; sequence: number; timestamp: string; kind: string; payload: unknown }

async function request<T>(path = '', body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}/v2/campaigns${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${getAccessToken() || ''}`, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`)
  }
  return response.json()
}

export function AgentCampaignPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [campaign, setCampaign] = useState<Campaign | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [prompt, setPrompt] = useState('Calculate QED and molecular weight for aspirin: CC(=O)Oc1ccccc1C(=O)O')
  const [reply, setReply] = useState('')
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; request<Campaign[]>().then(setCampaigns).catch(e => setError(e.message)); return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (!campaign) return
    const id = campaign.id
    let active = true
    const refresh = () => request<Event[]>(`/${id}/events`).then(data => { if (active) setEvents(data) }).catch(e => { if (active) setError(e.message) })
    refresh()
    const timer = window.setInterval(refresh, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [campaign?.id])

  async function run(current: Campaign) {
    // One durable model decision per request; a refresh can resume between steps.
    setCampaign(current)
    while (mounted.current && current.status === 'ready' && !current.busy) {
      current = await request<Campaign>(`/${current.id}/advance`, {})
      if (mounted.current) setCampaign(current)
    }
    if (mounted.current) {
      setEvents(await request<Event[]>(`/${current.id}/events`))
      setCampaigns(await request<Campaign[]>())
    }
  }
  async function perform(action: () => Promise<void>) {
    setWorking(true); setError('')
    try { await action() } catch (e) { setError(e instanceof Error ? e.message : 'Campaign request failed') }
    finally { if (mounted.current) setWorking(false) }
  }

  return <section className="agent-campaign">
    <h1>Scientific agent campaigns</h1>
    <p>Describe your objective and supply molecular structures. The agent can calculate descriptors, ask for clarification, and report results with evidence.</p>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={e => { e.preventDefault(); perform(async () => run(await request<Campaign>('', { message: prompt }))) }}>
      <label htmlFor="agent-prompt">New campaign</label><br />
      <textarea id="agent-prompt" value={prompt} onChange={e => setPrompt(e.target.value)} required maxLength={10000} rows={4} style={{ width: '100%' }} disabled={working} />
      <button disabled={working || !prompt.trim()} type="submit">Start campaign</button>
    </form>
    <label htmlFor="agent-history">Previous campaigns </label>
    <select id="agent-history" value={campaign?.id || ''} disabled={working} onChange={e => {
      const id = e.target.value
      if (id) perform(async () => { setEvents([]); setCampaign(await request<Campaign>(`/${id}`)) })
    }}><option value="">Select a campaign</option>{campaigns.map(c => <option key={c.id} value={c.id}>{c.id} — {c.status}</option>)}</select>
    {campaign && <>
      <h2>Campaign activity</h2>
      <p role="status">{campaign.busy || working ? 'Working' : campaign.status.replace(/_/g, ' ')} · {campaign.turns}/{campaign.max_turns} decisions</p>
      {campaign.error && <p role="alert">The agent step failed. Review the recorded activity; no automatic retry was submitted.</p>}
      {campaign.status === 'ready' && <button disabled={working || campaign.busy} onClick={() => perform(() => run(campaign))}>Continue campaign</button>}
      {campaign.question && <form onSubmit={e => { e.preventDefault(); perform(async () => {
        const next = await request<Campaign>(`/${campaign.id}/messages`, { message: reply }); setReply(''); await run(next)
      }) }}>
        <p>{campaign.question}</p><label htmlFor="agent-reply">Your answer</label><br />
        <textarea id="agent-reply" required maxLength={10000} value={reply} onChange={e => setReply(e.target.value)} disabled={working} />
        <button disabled={working || !reply.trim()} type="submit">Send answer</button>
      </form>}
      {campaign.report && <div><h2>Report</h2><p style={{ whiteSpace: 'pre-wrap' }}>{campaign.report.summary}</p>
        <p>Evidence: {campaign.report.evidence.map(id => <a key={id} href={`#result-${id}`} style={{ marginRight: 12 }}>{id}</a>)}</p></div>}
      {Object.entries(campaign.results).map(([id, result]) => <div id={`result-${id}`} key={id}>
        <h3>Result {id}</h3>
        {result.observations ? result.observations.map((observation, index) => <div key={index}>
          <p>{observation.canonical_smiles} · {observation.method} {observation.method_version} · computed</p>
          <table><thead><tr><th>Property</th><th>Value</th><th>Unit</th></tr></thead><tbody>{observation.values.map(value => <tr key={value.property}><td>{value.property}</td><td>{value.value.toPrecision(6)}</td><td>{value.unit}</td></tr>)}</tbody></table>
        </div>) : <pre>{JSON.stringify(result, null, 2)}</pre>}
      </div>)}
      <h2>Trace</h2>
      {events.map(event => <details key={event.event_id}><summary>{new Date(event.timestamp).toLocaleTimeString()} · {event.kind} · {event.action}</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(event.payload, null, 2)}</pre></details>)}
    </>}
  </section>
}
