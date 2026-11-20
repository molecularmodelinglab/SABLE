import { useEffect, useState } from 'react'
import { API_BASE, getAccessToken } from '../../api'
import { StructureViewer } from '../StructureViewer'
import { object, readable, Result } from './presentation'

export function predictionStructures(result: Result): Result[] {
  const outputs = result.kind === 'remote_batch_result' && Array.isArray(result.results) ? result.results.filter(object) : [result]
  return outputs.flatMap(output => {
    const measurements = object(output.measurement) ? [output.measurement] : Array.isArray(output.measurements) ? output.measurements.filter(object) : []
    return measurements.map(row => ({ ...row, provider: output.provider, target: output.protein_scope_id }))
  })
}

export function PredictionStructures({ result, campaignId }: { result: Result; campaignId: string }) {
  const rows = predictionStructures(result)
  const structures = rows.filter(row => object(row.structure) && typeof row.structure.id === 'string' && typeof row.structure.name === 'string')
  const [selected, setSelected] = useState('')
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState('')
  const row = structures.find(row => object(row.structure) && row.structure.id === selected)
  const artifact = row && object(row.structure) ? row.structure : null
  useEffect(() => {
    setContent(null); setError('')
    if (!selected) return
    const controller = new AbortController()
    const token = getAccessToken()
    fetch(`${API_BASE}/v2/campaigns/${encodeURIComponent(campaignId)}/artifacts/${encodeURIComponent(selected)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error('Unable to load this campaign structure')
      const value = await response.json()
      if (typeof value.content !== 'string') throw new Error('Invalid structure file')
      if (!controller.signal.aborted) setContent(value.content)
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [selected, campaignId])
  if (!rows.length) return null
  const missing = rows.length - structures.length
  return <section aria-label="Predicted structures">
    {structures.length > 0 && <label>Predicted structure <select value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">Choose a molecule to view in 3D</option>
      {structures.map((row, index) => { const file = row.structure as Record<string, unknown>; return <option key={`${file.id}-${index}`} value={String(file.id)}>{String(row.id)} · {String(row.provider)} · {String(row.target)}</option> })}
    </select></label>}
    {missing > 0 && <p className="workspace-muted">{missing} prediction(s) have no retained structure file. Their metrics remain available.</p>}
    {error && <p role="alert">{error}</p>}
    {selected && !content && !error && <p role="status">Loading structure…</p>}
    {content && artifact && row && <div className="campaign-structure-result">
      <StructureViewer filename={String(artifact.name)} content={content} />
      <aside><h4>{String(row.id)}</h4><p>{String(row.provider)} · {String(row.target)}</p><p>{String(row.smiles || '')}</p>
        <dl>{object(row.metrics) && Object.entries(row.metrics).map(([key, value]) => <div key={key}><dt>{key === 'affinity' ? 'Predicted affinity (log10 µM; lower is better)' : key === 'optimization_score' ? 'Platform optimization score (higher is better)' : readable(key)}</dt><dd>{String(value)}</dd></div>)}</dl>
        <a download={String(artifact.name)} href={`data:text/plain;charset=utf-8,${encodeURIComponent(content)}`}>Download structure</a>
      </aside>
    </div>}
  </section>
}
