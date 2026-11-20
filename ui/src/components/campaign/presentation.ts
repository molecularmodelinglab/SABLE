export type DataRow = Record<string, string | number | boolean | null>
export type Block =
  | { type: 'table'; title: string; columns: { key: string; label: string }[]; rows: DataRow[] }
  | { type: 'molecules'; title: string; items: { id: string; smiles: string; label?: string }[] }
  | { type: 'chart'; title: string; style: 'line' | 'scatter' | 'distribution'; x_label: string; y_label: string; points: { x: number; y: number; label?: string }[] }
export type Presentation = { version: 1; title: string; summary: string; blocks: Block[] }
export type Result = Record<string, unknown>
export const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const records = (value: unknown) => Array.isArray(value) ? value.filter(object) : []
export const readable = (value: string) => ({ tpsa: 'TPSA', logp: 'LogP', qed: 'QED', smiles: 'SMILES' }[value] || value.replace(/[_-]/g, ' ').replace(/^./, letter => letter.toUpperCase()))
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

function validPresentation(value: unknown): value is Presentation {
  if (!object(value) || value.version !== 1 || typeof value.title !== 'string' || typeof value.summary !== 'string' || !Array.isArray(value.blocks) || value.blocks.length > 12) return false
  return value.blocks.every(block => {
    if (!object(block) || typeof block.title !== 'string') return false
    if (block.type === 'table') return Array.isArray(block.columns) && block.columns.length <= 100 && block.columns.every(c => object(c) && typeof c.key === 'string' && typeof c.label === 'string') && Array.isArray(block.rows) && block.rows.length <= 10000 && block.rows.every(row => object(row) && Object.values(row).every(v => v === null || typeof v === 'string' || typeof v === 'boolean' || finite(v)))
    if (block.type === 'molecules') return Array.isArray(block.items) && block.items.length <= 1000 && block.items.every(row => object(row) && typeof row.id === 'string' && typeof row.smiles === 'string')
    return block.type === 'chart' && ['line', 'scatter', 'distribution'].includes(String(block.style)) && typeof block.x_label === 'string' && typeof block.y_label === 'string' && Array.isArray(block.points) && block.points.length <= 10000 && block.points.every(p => object(p) && finite(p.x) && finite(p.y))
  })
}

export function present(result: Result): Presentation {
  if (validPresentation(result.presentation)) return result.presentation
  const blocks: Block[] = []
  let title = 'Recorded result', summary = 'Open the source details for the full recorded output.'
  const candidates = records(result.candidates)
  if (Array.isArray(result.candidates)) {
    title = result.kind === 'recommendation' ? `Optimization round ${result.round} · recommended candidates` : 'Generated analogues'
    summary = `${candidates.length} candidates${Array.isArray(result.exclusions) ? ` · ${result.exclusions.length} excluded` : ''}.${result.stop_reason ? ` ${readable(String(result.stop_reason))}.` : ''}`
    const items = candidates.filter(c => typeof c.id === 'string' && typeof c.smiles === 'string').map(c => ({ id: String(c.id), smiles: String(c.smiles) }))
    blocks.push({ type: 'molecules', title: 'Molecule library', items }, { type: 'table', title: 'Candidates', columns: [{ key: 'id', label: 'Candidate' }, { key: 'smiles', label: 'SMILES' }], rows: items })
  }
  if (Array.isArray(result.matches)) {
    title = `Structure lookup · ${String(result.query || 'chemical name')}`
    summary = result.status === 'resolved' ? 'Structure retrieved from PubChem; identity and stereochemistry should match your intended compound.' : `Lookup ${readable(String(result.status || 'recorded')).toLowerCase()}.`
    blocks.push({ type: 'molecules', title: 'Retrieved structures', items: records(result.matches).filter(m => typeof m.smiles === 'string').map(m => ({ id: String(m.cid), label: `PubChem CID ${m.cid}`, smiles: String(m.smiles) })) })
  }
  const observations = records(result.observations)
  if (Array.isArray(result.observations)) {
    title = 'Molecular properties'
    summary = `${observations.length} structures evaluated${Array.isArray(result.failures) ? ` · ${result.failures.length} excluded` : ''}. Calculated descriptors are not experimental measurements.`
    const columns = new Map<string, string>([['molecule', 'Molecule'], ['smiles', 'SMILES']])
    const series = new Map<string, { label: string; values: number[] }>()
    const rows: DataRow[] = observations.map((obs, index) => {
      const row: DataRow = { molecule: String(obs.candidate_id || index + 1), smiles: String(obs.canonical_smiles || '') }
      for (const value of records(obs.values)) {
        if (typeof value.property !== 'string' || typeof value.unit !== 'string' || !finite(value.value)) continue
        const key = JSON.stringify([value.property, value.unit, obs.method, obs.method_version, obs.source])
        const label = `${readable(value.property)} (${value.unit}) · ${String(obs.method || 'recorded')} ${String(obs.method_version || '')}`
        columns.set(key, label); row[key] = value.value
        const data = series.get(key) || { label, values: [] }; data.values.push(value.value); series.set(key, data)
      }
      return row
    })
    blocks.push({ type: 'table', title: 'Descriptor values', columns: [...columns].map(([key, label]) => ({ key, label })), rows })
    blocks.push({ type: 'molecules', title: 'Evaluated molecules', items: rows.filter(row => row.smiles).map(row => ({ id: String(row.molecule), smiles: String(row.smiles) })) })
    // Methods and units remain separate; a chart selector keeps many properties manageable.
    for (const data of series.values()) if (data.values.length > 1) blocks.push({ type: 'chart', style: 'distribution', title: data.label, x_label: data.label, y_label: 'Molecule count', points: data.values.map(value => ({ x: value, y: 1 })) })
  }
  if (Array.isArray(result.rankings)) {
    title = 'Candidate ranking'
    summary = `${result.evaluated_candidates ?? records(result.rankings).length} candidates evaluated${result.library_size ? ` from a library of ${result.library_size}` : ''}. Rankings reflect recorded computational objectives.`
    const names = new Set<string>()
    const rows: DataRow[] = records(result.rankings).map(row => {
      const values: DataRow = { molecule: String(row.molecule_id || ''), smiles: String(row.smiles || ''), rank: finite(row.rank) ? row.rank : null, pareto_front: finite(row.pareto_front) ? row.pareto_front : null }
      if (object(row.objective_values)) for (const [name, value] of Object.entries(row.objective_values)) if (finite(value)) { names.add(name); values[`objective:${name}`] = value }
      return values
    })
    blocks.push({ type: 'table', title: 'Ranked candidates', columns: [{ key: 'molecule', label: 'Candidate' }, { key: 'rank', label: 'Rank' }, { key: 'pareto_front', label: 'Pareto front' }, { key: 'smiles', label: 'SMILES' }, ...[...names].map(name => ({ key: `objective:${name}`, label: readable(name) }))], rows })
    blocks.push({ type: 'molecules', title: 'Ranked molecules', items: rows.filter(row => row.smiles).map(row => ({ id: String(row.molecule), smiles: String(row.smiles), label: `${row.rank === null ? 'Unranked' : `Rank ${row.rank}`} · ${row.molecule}` })) })
  }
  if (['boltz_prediction', 'boltz_screen', 'remote_batch_result'].includes(String(result.kind))) {
    const outputs = result.kind === 'remote_batch_result' ? records(result.results) : [result]
    const rows: DataRow[] = []
    for (const output of outputs) {
      const measurements = output.kind === 'boltz_prediction' && object(output.measurement) ? [output.measurement] : records(output.measurements)
      for (const measurement of measurements) {
        if (!object(measurement.metrics)) continue
        for (const [metric, value] of Object.entries(measurement.metrics)) if (finite(value)) rows.push({
          molecule: String(measurement.id), smiles: String(measurement.smiles), provider: String(output.provider),
          target: String(output.protein_scope_id), metric: metric === 'affinity' ? 'Predicted affinity (log10 µM; lower is better)' : metric === 'optimization_score' ? 'Platform optimization score (higher is better)' : readable(metric), value,
        })
      }
    }
    const failures = records(result.failures).length + outputs.reduce((count, output) => count + records(output.exclusions).length, 0)
    title = 'Binding predictions'
    summary = `Computational predictions, not measured affinity. Confidence and provider ranking scores are distinct metrics.${failures ? ` ${failures} calculation(s) failed or were excluded; inspect Calculations for details.` : ''}`
    blocks.push({ type: 'table', title: 'Prediction metrics', columns: ['molecule', 'smiles', 'provider', 'target', 'metric', 'value'].map(key => ({ key, label: readable(key) })), rows })
  }
  if (result.kind === 'remote_batch') { title = 'Batch calculations submitted'; summary = `${records(result.jobs).length} molecule jobs queued. Results appear when the batch finishes; individual progress is available in Calculations.` }
  if (result.kind === 'job_outcome') { title = 'Remote calculation outcome'; summary = `Job ${readable(String(result.status)).toLowerCase()}. Review job details before using this as evidence.` }
  if (result.kind === 'remote_job') { title = 'Calculation submitted'; summary = 'Waiting for the remote service. Results will appear here when available.' }
  if (!blocks.length) {
    const scalar = Object.entries(result).filter(([, value]) => value === null || typeof value === 'string' || typeof value === 'boolean' || finite(value))
    if (scalar.length) blocks.push({ type: 'table', title: 'Summary', columns: [{ key: 'property', label: 'Property' }, { key: 'value', label: 'Value' }], rows: scalar.filter(([key]) => key !== 'figure_png_base64').map(([key, value]) => ({ property: readable(key), value: value as DataRow[string] })) })
  }
  return { version: 1, title, summary, blocks }
}

export function csv(block: Extract<Block, { type: 'table' }>) {
  const cell = (value: unknown) => { let text = String(value ?? ''); if (typeof value === 'string' && /^[=+@\-\t\r]/.test(text)) text = "'" + text; return `"${text.replace(/"/g, '""')}"` }
  return [block.columns.map(c => cell(c.label)).join(','), ...block.rows.map(row => block.columns.map(c => cell(row[c.key])).join(','))].join('\r\n')
}
