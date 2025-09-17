import { useEffect, useMemo, useState } from 'react'
import { createRun, listRuns, getRun, openEventStream, API_BASE, type RunInfo } from './api'

function NewRunForm({ onCreated }: { onCreated: (r: RunInfo) => void }) {
  const [prompt, setPrompt] = useState('Optimize aspirin for better QED. Enumerate 50 analogs and run 3 iterations.')
  const [batch, setBatch] = useState<number | ''>('' as any)
  const [iters, setIters] = useState<number | ''>('' as any)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const disabled = busy || !prompt.trim()
  const promptHint = !prompt.trim() ? 'Enter a prompt with targets, e.g., "Optimize aspirin for higher QED and lower TPSA"' : ''

  return (
    <div style={{ border: '1px solid #ddd', padding: 12, borderRadius: 6 }}>
      <h3>Start New Optimization</h3>
  <textarea value={prompt} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setPrompt(e.target.value)} rows={4} style={{ width: '100%' }} placeholder={promptHint} />
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
  <input type="number" placeholder="Batch size" value={batch as any} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setBatch(e.target.value ? Number(e.target.value) : '' as any)} />
  <input type="number" placeholder="Max iterations" value={iters as any} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setIters(e.target.value ? Number(e.target.value) : '' as any)} />
        <button disabled={disabled} onClick={async () => {
          try { setBusy(true); setErr(null); const r = await createRun(prompt, iters || undefined, batch || undefined); onCreated(r); }
      catch (e: any) { setErr(e.message || String(e)); setToast('Failed to start run'); setTimeout(() => setToast(null), 3000); }
          finally { setBusy(false); }
        }}>Start</button>
      </div>
      {err && <div style={{ color: 'crimson', marginTop: 6 }}>{err}</div>}
    {toast && <div style={{ position: 'fixed', top: 12, right: 12, background: '#333', color: '#fff', padding: '8px 12px', borderRadius: 6 }}>{toast}</div>}
    </div>
  )
}

function RunsList({ runs, onSelect }: { runs: RunInfo[], onSelect: (id: string) => void }) {
  return (
    <div>
      <h3>Recent Runs</h3>
      <ul>
        {runs.map(r => (
          <li key={r.id}>
            <button onClick={() => onSelect(r.id)}>{r.id}</button>
            &nbsp;— {r.status} — {new Date(r.created_at).toLocaleString()}
          </li>
        ))}
      </ul>
    </div>
  )
}

function RunDetail({ id }: { id: string }) {
  const [info, setInfo] = useState<RunInfo | null>(null)
  const [events, setEvents] = useState<any[]>([])
  useEffect(() => {
    let stop = false
    const tick = async () => {
      try { const r = await getRun(id); if (!stop) setInfo(r) } catch {}
    }
    tick()
    const t = setInterval(tick, 2000)
    const close = openEventStream(id, (evt) => setEvents(prev => [...prev.slice(-200), evt]))
    return () => { stop = true; clearInterval(t); close() }
  }, [id])

  if (!info) return <div>Loading...</div>

  return (
    <div style={{ border: '1px solid #ddd', padding: 12, borderRadius: 6 }}>
      <h3>Run {id}</h3>
      <div>Status: {info.status}</div>
      {info.exit_reason && <div>Exit: {info.exit_reason}</div>}
      <div>Created: {new Date(info.created_at).toLocaleString()}</div>
      <div>Updated: {new Date(info.updated_at).toLocaleString()}</div>
      <div style={{ marginTop: 8 }}>
        {info.results_available && <a href={`http://localhost:8000/runs/${id}/artifacts/results.json`} target="_blank">results.json</a>}
        {' '}
        {info.summary_available && <a href={`http://localhost:8000/runs/${id}/artifacts/summary.txt`} target="_blank">summary.txt</a>}
      </div>
      <h4 style={{ marginTop: 12 }}>Live logs</h4>
      <div style={{ maxHeight: 240, overflow: 'auto', background: '#fafafa', border: '1px solid #eee', padding: 8 }}>
        <ul style={{ margin: 0, paddingLeft: 16 }}>
          {events.slice(-100).map((e, i) => (
            <li key={i}>
              <code>{e.action || e.event}</code>
              {e.data && <span> — {typeof e.data === 'string' ? e.data : JSON.stringify(e.data)}</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

export function App() {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const refresh = async () => {
    try { const r = await listRuns(); setRuns(r.runs) } catch {}
  }
  useEffect(() => { refresh(); const t = setInterval(refresh, 2000); return () => clearInterval(t) }, [])

  return (
    <div style={{ maxWidth: 960, margin: '24px auto', fontFamily: 'Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif' }}>
      <h2 style={{ marginBottom: 8 }}>ANOLE</h2>
      <div style={{ color: '#666', marginBottom: 16 }}>Adaptive Navigation for Open‑ended Ligand Exploration</div>
      <NewRunForm onCreated={r => { setSelected(r.id); refresh() }} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 16, marginTop: 16 }}>
        <RunsList runs={runs} onSelect={setSelected} />
        {selected ? <RunDetail id={selected} /> : <div>Select a run</div>}
      </div>
    </div>
  )
}
